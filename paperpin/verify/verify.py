"""VERIFICATION stage (§6.1/§6.6): independent proofs stack up per field.

Order of application:
  1. canonical re-comparison — evidence text ≡ value under type canon
     (the aligner's match already guarantees this; re-checked here defensively)
  2. checksums — IBAN mod-97, EAN check digit, VAT formats (+SK/PL checksums),
     date plausibility; sets proof="checksum" on success and can REPAIR
     confusable-corrupted ids (E-14)
  3. arithmetic cross-checks — invoice math (§6.6.4)
  4. quote check (§6.6.5) — a model quote that matches nothing on the page is
     a strong hallucination signal even when the value itself was found
"""
from __future__ import annotations

from typing import Optional

from ..align.canon import canon_value
from ..align.matchers import value_date_set, value_number_set
from ..align.rows import Row
from ..types import FieldResult, FieldSpec, FieldType, Status
from .arithmetic import run_arithmetic
from .checksums import date_plausible, ean_check_digit, iban_check, vat_check


def _canonical_recheck(spec: FieldSpec, fr: FieldResult) -> Optional[bool]:
    if fr.evidence is None or fr.value is None:
        return None
    if spec.type in (FieldType.NUMBER, FieldType.PERCENT):
        doc = value_number_set(fr.evidence)
        want = value_number_set(fr.value)
        return bool({abs(d) for d in doc} & {abs(w) for w in want}) if (doc and want) else None
    if spec.type == FieldType.DATE:
        doc = value_date_set(fr.evidence)
        want = value_date_set(str(fr.value))
        return bool(doc & want) if (doc and want) else None
    if spec.type == FieldType.ID:
        return canon_value(str(fr.value)) in canon_value(fr.evidence) or \
               canon_value(fr.evidence) in canon_value(str(fr.value))
    return None  # text/block: the match itself is the comparison


def _checksum_pass(spec: FieldSpec, fr: FieldResult) -> None:
    kind = spec.checksum
    if kind == "iban":
        passed, repaired, note = iban_check(str(fr.value))
        if passed is None:
            return
        if passed and repaired is None:
            fr.proof = "checksum"
            fr.notes.append(note)
            # the checksum proves the VALUE; confidence describes the
            # LOCATION — a fuzzy-located box must not read as near-certain
            if fr.status == Status.VERIFIED:
                fr.confidence = max(fr.confidence, 0.99)
        elif passed and repaired:
            fr.proof = "checksum"
            if fr.status == Status.VERIFIED:  # ambiguous stays ambiguous
                fr.status = Status.LOW_CONFIDENCE
            fr.notes.append(note)
            fr.repaired_value = repaired
        else:
            fr.status = Status.LOW_CONFIDENCE if fr.status == Status.VERIFIED else fr.status
            fr.notes.append(f"⚠ {note}")
    elif kind == "ean":
        ok = ean_check_digit(str(fr.value))
        if ok is True:
            fr.proof = "checksum"
            fr.notes.append("EAN check digit passed")
            if fr.status == Status.VERIFIED:
                fr.confidence = max(fr.confidence, 0.99)
        elif ok is False:
            fr.status = Status.LOW_CONFIDENCE if fr.status == Status.VERIFIED else fr.status
            fr.notes.append("⚠ EAN check digit FAILED")
    elif kind == "vat":
        ok, note = vat_check(str(fr.value))
        if ok is True and note:
            if "checksum passed" in note:
                fr.proof = "checksum"
                if fr.status == Status.VERIFIED:
                    fr.confidence = max(fr.confidence, 0.99)
            fr.notes.append(note)
        elif ok is False and note:
            fr.status = Status.LOW_CONFIDENCE if fr.status == Status.VERIFIED else fr.status
            fr.notes.append(f"⚠ {note}")


def _quote_check(fr: FieldResult, rows: list[Row]) -> None:
    if not fr.quote:
        return
    q = canon_value(str(fr.quote))
    if not q:
        return
    # quotes may span rows (addresses) and OCR may fold a character — search
    # per-page canon and tolerate near-matches before flagging
    pages: dict[int, list[str]] = {}
    for row in rows:
        if q in row.canon:
            return
        pages.setdefault(row.page, []).append(row.canon)
    from difflib import SequenceMatcher
    for page_canon in ("".join(parts) for parts in pages.values()):
        if q in page_canon:
            return
        # one mid-quote OCR confusable halves the longest contiguous run —
        # judge by total matched material instead
        sm = SequenceMatcher(None, page_canon, q)
        matched = sum(b.size for b in sm.get_matching_blocks())
        if matched >= max(4, 0.85 * len(q)):
            return
    fr.notes.append("⚠ model quote matches nothing on the page — transcription suspect")
    if fr.status == Status.VERIFIED:
        fr.status = Status.LOW_CONFIDENCE


def verify_results(results: dict[str, FieldResult], specs: dict[str, FieldSpec],
                   rows: list[Row], route_by_page: dict[int, str],
                   page_image_provider=None, ocr_backend=None) -> None:
    # arithmetic first: an equation that holds is an independent proof, and a
    # proven field needs no crop re-read (skip = fewer false demotions + CPU).
    # Only LOCATED values participate — an equation whose operands match
    # nothing on the page proves nothing, however self-consistent the model
    # was when it invented them.
    located = {n: fr.value for n, fr in results.items()
               if fr.status not in (Status.NOT_FOUND, Status.NOT_PRESENT)}
    arithmetic_notes = run_arithmetic(located, specs)

    for name, fr in results.items():
        spec = specs.get(name) or FieldSpec(name=name)
        if fr.status in (Status.NOT_PRESENT, Status.NOT_FOUND):
            _quote_check(fr, rows)
            continue
        if fr.method is None and fr.page is not None:
            fr.method = route_by_page.get(fr.page, "ocr")

        recheck = _canonical_recheck(spec, fr)
        if recheck is False:
            if fr.status == Status.VERIFIED:  # ambiguous stays ambiguous
                fr.status = Status.LOW_CONFIDENCE
            fr.notes.append("⚠ canonical re-comparison failed (evidence ≠ value)")

        if spec.type == FieldType.DATE and fr.value is not None:
            plausible = date_plausible(str(fr.value))
            if plausible is False:
                if fr.status == Status.VERIFIED:
                    fr.status = Status.LOW_CONFIDENCE
                fr.notes.append("⚠ date is outside the plausible window (±10y)")

        _checksum_pass(spec, fr)
        _quote_check(fr, rows)

        # crop re-read (§6.6.2): OCR-routed verified fields only — the pixels
        # under the box must read back as the matched evidence. Fields already
        # proven by a checksum or by document math skip it.
        math_proved = ((fr.proof == "checksum" and fr.repaired_value is None)
                       or any("arithmetic passed" in x for x in fr.notes)
                       or any("arithmetic passed" in x
                              for x in arithmetic_notes.get(name, ())))
        if (not math_proved
                and fr.status == Status.VERIFIED and fr.bbox is not None
                and fr.page is not None and fr.evidence
                and route_by_page.get(fr.page) == "ocr"
                and page_image_provider is not None and ocr_backend is not None):
            from .crop_reread import reread_agrees, reread_crop
            single_line = spec.type in (FieldType.NUMBER, FieldType.PERCENT,
                                        FieldType.DATE)
            reread = reread_crop(page_image_provider(fr.page), fr.bbox,
                                 ocr_backend, single_line=single_line)
            if reread is not None and not reread_agrees(spec, fr.evidence, reread):
                fr.status = Status.LOW_CONFIDENCE
                fr.notes.append(f"⚠ crop re-read disagreed — box re-reads as "
                                f"{reread[:60]!r}, expected {fr.evidence[:40]!r}")
            elif reread is not None:
                fr.notes.append("crop re-read confirmed the pixels under the box")

    for name, notes in arithmetic_notes.items():
        if name in results:
            fr = results[name]
            fr.notes.extend(notes)
            if any(n.startswith("⚠") for n in notes) and fr.status == Status.VERIFIED:
                fr.notes.append("value located on page but the document math disagrees")
            elif any("arithmetic passed" in n for n in notes) and fr.proof is None:
                fr.proof = "arithmetic"
