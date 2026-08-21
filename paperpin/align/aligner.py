"""ALIGNMENT stage (§6.1): pure deterministic value→location linking.

For each extracted field: run the type matcher over all visual rows (plus
cross-row merges when single rows almost-miss, E-15), anchor-score every
candidate (§6.5), then decide:

  null value                → not_present (the model says it isn't there)
  no candidate              → not_found  (the hallucination flag)
  clear best, exact         → verified   (pending the verification stack)
  clear best, fuzzy         → low_confidence
  top candidates tied       → ambiguous  (ALL candidates reported)
"""
from __future__ import annotations

from typing import Optional

from ..types import Candidate, FieldResult, FieldSpec, FieldType, Status
from .anchors import AMBIGUITY_EPSILON, anchors_for, score_anchor
from .matchers import (RawMatch, match_block, match_date, match_id, match_number,
                       match_text)
from .rows import Row, cross_row_candidates


def _alias_prints(spec: FieldSpec, value) -> list[str]:
    """Alternate literal prints the schema declares for this exact value."""
    if not spec.aliases or value is None:
        return []
    from .canon import canon_value
    v = canon_value(str(value))
    raw = str(value).strip()
    out: list[str] = []
    for key, prints in spec.aliases.items():
        if raw == key or (v and canon_value(key) == v):
            out.extend(prints)
    return out


def _run_matcher(spec: FieldSpec, rows: list[Row], value) -> list[RawMatch]:
    def one(v) -> list[RawMatch]:
        if spec.type == FieldType.NUMBER:
            return match_number(rows, v)
        if spec.type == FieldType.PERCENT:
            return match_number(rows, v, percent=True)
        if spec.type == FieldType.DATE:
            return match_date(rows, v)
        if spec.type == FieldType.ID:
            return match_id(rows, v, pattern=spec.pattern)
        if spec.type == FieldType.BLOCK:
            return match_block(rows, v)
        return match_text(rows, v)

    matches = one(value)
    for alt in _alias_prints(spec, value):
        matches.extend(one(alt))
    return matches


def _is_short_number(value) -> bool:
    from .canon import canon_value
    c = canon_value(str(value))
    return len(c) <= 2 and c.isdigit()


def _bbox_iou(a: tuple[float, float, float, float],
              b: tuple[float, float, float, float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1e-12, area_a + area_b - inter)


def align_fields(rows: list[Row],
                 page_sizes: dict[int, tuple[float, float]],
                 extraction: dict,
                 specs: dict[str, FieldSpec]) -> dict[str, FieldResult]:
    merged_rows: Optional[list[Row]] = None  # built lazily — it's O(rows)

    results: dict[str, FieldResult] = {}
    for name, value in extraction.items():
        spec = specs.get(name) or FieldSpec(name=name)
        quote = None
        if isinstance(value, dict) and "value" in value:  # quote-then-extract shape
            quote = value.get("quote")
            value = value.get("value")

        if value is None or (isinstance(value, str) and not value.strip()):
            results[name] = FieldResult(name=name, value=None,
                                        status=Status.NOT_PRESENT, confidence=1.0,
                                        quote=quote,
                                        notes=["model returned null — field absent"])
            continue

        # BLOCK values are inherently multi-row — searching only single rows
        # would bias toward one-line repeats (footers). Everything else tries
        # cross-row merges only when single rows come up empty.
        if spec.type == FieldType.BLOCK:
            if merged_rows is None:
                merged_rows = cross_row_candidates(rows)
            raw_matches = _run_matcher(spec, rows + merged_rows, value)
        else:
            raw_matches = _run_matcher(spec, rows, value)
            if not raw_matches:
                if merged_rows is None:
                    merged_rows = cross_row_candidates(rows)
                raw_matches = _run_matcher(spec, merged_rows, value)
            if (not raw_matches and spec.type == FieldType.TEXT
                    and len(str(value).split()) >= 2):
                # names glued from non-contiguous prints ("Jollibee" + a
                # "c/o …" line further down): no contiguous window exists —
                # the token-union matcher still finds the dominant cluster.
                # Single-token values stay out: their one token would match
                # inside any canon run ("CZK" in "cz, kontakt").
                raw_matches = match_block(rows + merged_rows, value)

        if not raw_matches:
            results[name] = FieldResult(
                name=name, value=value, status=Status.NOT_FOUND, confidence=0.0,
                quote=quote,
                notes=["value matches nothing on the document — possible hallucination"])
            continue

        candidates = _score_candidates(name, spec, raw_matches, rows, page_sizes,
                                       quote=quote, value=value)
        results[name] = _decide(name, value, quote, candidates, spec=spec)

    _resolve_shared_instances(results)
    _bind_affinities(results, specs)
    return results


# The model's quote (§4.4) names the exact line it read the value from. A
# candidate whose row matches that context IS the instance the value came
# from — decisive over label anchors (0.35) and position priors (0.05).
# Still deterministic: the quote is text, checked against the document's own
# rows; a quote matching nothing simply adds no score anywhere.
QUOTE_BONUS = 1.0


def _quote_context_score(quote_ctx: str, value_canon: str, m: RawMatch) -> float:
    """The discriminating part of a quote is what surrounds the value — the
    value itself sits in every candidate row by construction. Compare the
    quote with the value stripped out against the row with the value stripped
    out ("karte" vs "artikel"), otherwise shared digits drown the label."""
    row_ctx = m.row.canon
    if value_canon:
        row_ctx = row_ctx.replace(value_canon, "", 1)
    if not quote_ctx or not row_ctx:
        return 0.0
    if quote_ctx in row_ctx or (len(row_ctx) >= 3 and row_ctx in quote_ctx):
        return QUOTE_BONUS
    from .canon import ratio
    if ratio(quote_ctx, row_ctx) >= 0.75:
        return QUOTE_BONUS
    return 0.0


def _score_candidates(name: str, spec: FieldSpec, raw: list[RawMatch],
                      rows: list[Row], page_sizes: dict[int, tuple[float, float]],
                      quote: Optional[str] = None, value=None) -> list[Candidate]:
    anchor_canons = anchors_for(name, spec.anchors)
    from .canon import canon_value
    quote_ctx = ""
    value_canon = ""
    if quote:
        value_canon = canon_value(str(value)) if value is not None else ""
        quote_ctx = canon_value(quote)
        if value_canon:
            quote_ctx = quote_ctx.replace(value_canon, "", 1)
        if len(quote_ctx) < 3:  # value-only quote — no location context in it
            quote_ctx = ""
    out: list[Candidate] = []
    for m in raw:
        bbox_px = m.bbox_px()
        if bbox_px is None:
            continue
        page = m.row.page
        w, h = page_sizes[page]
        norm = (bbox_px[0] / w, bbox_px[1] / h, bbox_px[2] / w, bbox_px[3] / h)
        norm = tuple(min(1.0, max(0.0, v)) for v in norm)
        a_score, a_text = score_anchor(m, rows, anchor_canons)
        if quote_ctx:
            a_score += _quote_context_score(quote_ctx, value_canon, m)
        out.append(Candidate(page=page, bbox=norm, score=m.score,
                             exact=m.exact, fused=m.fused,
                             evidence=m.matched_text,
                             anchor=a_text, anchor_score=a_score))
    # dedupe identical boxes (single-row hit often reappears in merged rows)
    deduped: list[Candidate] = []
    for c in sorted(out, key=lambda c: -c.total_score):
        if all(c.page != d.page or _bbox_iou(c.bbox, d.bbox) < 0.9 for d in deduped):
            deduped.append(c)
    return deduped


def _same_value_print(spec, value, evidence: str) -> bool:
    """Is this evidence just the value in different clothes? Canon equality,
    or any schema-declared alias print (whose canon may be empty — '€')."""
    from .canon import canon_value
    if canon_value(evidence) == canon_value(str(value)):
        return True
    stripped = evidence.strip(" ()[]:;,")
    return any(stripped == alt for alt in _alias_prints(spec, value))


def _decide(name: str, value, quote, candidates: list[Candidate],
            spec: Optional[FieldSpec] = None) -> FieldResult:
    if not candidates:
        return FieldResult(name=name, value=value, status=Status.NOT_FOUND,
                           confidence=0.0, quote=quote,
                           notes=["matched region had no measurable box"])
    best = candidates[0]
    tied = [c for c in candidates[1:]
            if best.total_score - c.total_score < AMBIGUITY_EPSILON
            and (c.page != best.page or _bbox_iou(best.bbox, c.bbox) < 0.5)]
    if tied:
        from ..align.canon import canon_value
        # E-23: the same VALUE printed several times is not ambiguity — the
        # value is on the document either way. Pin the strongest instance,
        # keep every instance listed. Ambiguous is reserved for ties between
        # locations with genuinely different content.
        same = spec is not None and all(
            _same_value_print(spec, value, c.evidence) for c in [best, *tied])
        if same or all(canon_value(c.evidence) == canon_value(best.evidence)
                       for c in tied):
            # tiebreak within equal-value instances: reading order (topmost
            # first) — the canonical print beats footer/summary repeats
            instances = [best, *tied]
            best = min(instances, key=lambda c: (c.page, c.bbox[1]))
            tied = [c for c in instances if c is not best]
            status = Status.VERIFIED if best.exact else Status.LOW_CONFIDENCE
            conf = best.score if best.exact else best.score * 0.9
            notes = [f"value appears {1 + len(tied)}× on the document — "
                     "strongest instance pinned, all instances listed"]
            # E-22 guard applies here too (a bare "12" matching twice is
            # still a bare "12")
            if (status == Status.VERIFIED and best.anchor is None
                    and _is_short_number(value)):
                status = Status.LOW_CONFIDENCE
                conf = min(conf, 0.6)
                notes.append("short value with no supporting label nearby — "
                             "location is a guess, human should glance")
            return FieldResult(
                name=name, value=value, status=status, confidence=conf,
                page=best.page, bbox=best.bbox, evidence=best.evidence,
                anchor=best.anchor, quote=quote, candidates=[best, *tied],
                notes=notes)
        return FieldResult(
            name=name, value=value, status=Status.AMBIGUOUS,
            confidence=max(0.3, best.score - 0.2 * len(tied)),
            page=best.page, bbox=best.bbox, evidence=best.evidence,
            anchor=best.anchor, quote=quote, candidates=[best, *tied],
            notes=[f"{1 + len(tied)} equally plausible locations — anchors could not break the tie"])
    status = Status.VERIFIED if best.exact else Status.LOW_CONFIDENCE
    conf = best.score if best.exact else best.score * 0.9
    notes = [] if best.exact else ["fuzzy match — human should glance"]
    # E-22 guard: a 1–2 digit number is too common to trust on geometry alone.
    # Without a label anchor near it, it never passes as verified.
    if (status == Status.VERIFIED and best.anchor is None
            and _is_short_number(value)):
        status = Status.LOW_CONFIDENCE
        conf = min(conf, 0.6)
        notes.append("short value with no supporting label nearby — "
                     "location is a guess, human should glance")
    # a span GLUED from separate printed runs ('24' + '158,97' fusing into
    # 24158.97) is a plausible reading, not a certain one — the module note
    # says anchors disambiguate, so absent an anchor it never wears verified
    if status == Status.VERIFIED and best.fused and best.anchor is None:
        status = Status.LOW_CONFIDENCE
        conf = min(conf, 0.75)
        notes.append("value spans two separately printed numbers — fused "
                     "reading with no supporting label, human should glance")
    return FieldResult(name=name, value=value, status=status, confidence=conf,
                       page=best.page, bbox=best.bbox, evidence=best.evidence,
                       anchor=best.anchor, quote=quote,
                       candidates=candidates[:5], notes=notes)


def _bind_affinities(results: dict[str, FieldResult],
                     specs: dict[str, FieldSpec]) -> None:
    """A field whose schema declares `affinity` prints ON its target's line
    (a currency mark beside its total) while repeating elsewhere; the instance
    on the pinned target's own line is the semantic one. Acts only inside the
    equal-value tie set (within AMBIGUITY_EPSILON of the best candidate), so a
    quote- or anchor-backed clear winner never moves. Fully schema-driven —
    the engine knows no field names."""
    for name, fr in results.items():
        spec = specs.get(name)
        if (spec is None or not spec.affinity
                or fr.bbox is None or len(fr.candidates) < 2):
            continue
        target = next((results[t] for t in spec.affinity
                       if t in results and results[t].bbox is not None), None)
        if target is None:
            continue
        best = max(c.total_score for c in fr.candidates)
        ties = [c for c in fr.candidates
                if best - c.total_score < AMBIGUITY_EPSILON]
        t_cy = (target.bbox[1] + target.bbox[3]) / 2
        t_h = target.bbox[3] - target.bbox[1]

        def on_target_line(c: Candidate) -> bool:
            if c.page != target.page:
                return False
            cy = (c.bbox[1] + c.bbox[3]) / 2
            return abs(cy - t_cy) < 0.7 * max(c.bbox[3] - c.bbox[1], t_h)

        on_line = [c for c in ties if on_target_line(c)]
        if not on_line:
            continue
        pick = max(on_line, key=lambda c: c.total_score)
        if pick.bbox == fr.bbox:
            continue
        fr.bbox, fr.page = pick.bbox, pick.page
        fr.evidence, fr.anchor = pick.evidence, pick.anchor
        # the reported numbers must describe the candidate actually pinned
        fr.confidence = min(fr.confidence, pick.score)
        if not pick.exact and fr.status == Status.VERIFIED:
            fr.status = Status.LOW_CONFIDENCE
        fr.notes.append(f"bound to the pinned {target.name}'s line "
                        "(the value follows its companion field)")


def _resolve_shared_instances(results: dict[str, FieldResult]) -> None:
    """§6.5.3: two fields pinned to the same box is only OK when their values
    agree — note the sharing; when they disagree, try each field's next-best
    distinct candidate."""
    from .canon import canon_value
    placed = [(n, r) for n, r in results.items() if r.bbox is not None]
    for i, (name_a, a) in enumerate(placed):
        for name_b, b in placed[i + 1:]:
            if a.page != b.page or _bbox_iou(a.bbox, b.bbox) < 0.85:
                continue
            # spelling variants of one value ('90.00' / '90,00') agree —
            # raw string equality routed them into the disagree path
            if (str(a.value) == str(b.value)
                    or canon_value(str(a.value)) == canon_value(str(b.value))):
                note = f"shares its box with field '{name_b}' (equal values)"
                if note not in a.notes:
                    a.notes.append(note)
                    b.notes.append(f"shares its box with field '{name_a}' (equal values)")
                continue
            moved = False
            for mover in sorted((a, b), key=lambda r: -len(r.candidates)):
                other = a if mover is b else b
                for alt in mover.candidates[1:]:
                    if _bbox_iou(alt.bbox, other.bbox) < 0.5:
                        mover.bbox, mover.page = alt.bbox, alt.page
                        mover.evidence, mover.anchor = alt.evidence, alt.anchor
                        # the pinned candidate changed — the reported numbers
                        # must describe IT, not the one it displaced
                        mover.confidence = min(mover.confidence, alt.score)
                        if not alt.exact and mover.status == Status.VERIFIED:
                            mover.status = Status.LOW_CONFIDENCE
                        mover.notes.append("reassigned to a distinct instance (box collision)")
                        moved = True
                        break
                if moved:
                    break
            if not moved:
                # nowhere to move either field: at most one of them is right,
                # and staying silent left both clean verified (§6.5.3)
                for fr, other_name in ((a, name_b), (b, name_a)):
                    note = (f"shares its box with field '{other_name}' "
                            "but the values disagree — at most one is right")
                    if note in fr.notes:
                        continue
                    if fr.status == Status.VERIFIED:
                        fr.status = Status.LOW_CONFIDENCE
                        fr.confidence = min(fr.confidence, 0.6)
                    fr.notes.append(note)
