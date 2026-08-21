"""Table / line-item grounding (E-30).

The model returns an ARRAY of row objects; each extraction row is matched to
the visual document row where the most of its cells co-occur (rows can come
back reordered — order is never assumed). Cells are then pinned within that
row (wrapped descriptions may fall back to the adjacent row, E-31), and each
row gets the qty × unit_price ≈ amount arithmetic check.

Results are emitted as flat fields — `line_items[0].qty` — so every cell is
an ordinary FieldResult with its own box and status.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

from ..types import Candidate, FieldResult, FieldSpec, FieldType, Status
from .aligner import _is_short_number, _run_matcher
from .anchors import anchors_for
from .canon import find_all
from .rows import Row, merged_row

# how far an item's territory may stretch past the outermost assigned rows:
# a receipt item spans a few printed lines (description, EAN, numbers), but
# never the letterhead above the table or the footer below it (E-30b)
TERRITORY_EDGE_ROWS = 2


def align_table(name: str, spec: FieldSpec, row_objects: list,
                rows: list[Row], page_sizes: dict[int, tuple[float, float]]
                ) -> dict[str, FieldResult]:
    results: dict[str, FieldResult] = {}
    if not isinstance(row_objects, list):
        return results
    columns = spec.columns or {}
    # assignment works on whole printed lines; column cells are extra views of
    # the same line and would let two duplicates claim one physical row twice
    rows = [r for r in rows if not r.is_cell]

    # score every (extraction row, doc row) pair once — the assignment must be
    # one-to-one, and duplicated products (the same item printed N times) can
    # only be told apart by WHERE they sit, not by what they say
    bands_by_page = _column_bands(columns, rows)

    item_idxs = [i for i, obj in enumerate(row_objects) if isinstance(obj, dict)]
    pair_hits: dict[tuple[int, int], dict] = {}
    pair_score: dict[tuple[int, int], float] = {}
    # the full matcher on every (item x row) pair is quadratic and measured
    # in minutes on statement-sized tables — a row can only host an item if
    # it shares a 3+ char alnum run with SOME cell value, which a set
    # intersection answers instantly
    from .canon import canon_value

    def grams(text: str) -> set[str]:
        # <3 canon chars have no trigram: contribute nothing rather than a
        # short gram that can never intersect a trigram set (a short-only
        # item must NOT be prefiltered away — it has no evidence to prune on)
        c = canon_value(str(text))
        return {c[k:k + 3] for k in range(len(c) - 2)} if len(c) >= 3 else set()

    row_grams = [grams(row.text) for row in rows]
    for i in item_idxs:
        obj = row_objects[i]
        item_grams: set[str] = set()
        for col in columns:
            v = obj.get(col)
            if v is not None:
                item_grams |= grams(v)
        for r_i, row in enumerate(rows):
            # an item with no trigram at all (all-short cells) has no
            # evidence to prune on and must reach the matcher for every row
            if item_grams and not (item_grams & row_grams[r_i]):
                continue
            hits = _row_cell_hits(obj, columns, row,
                                  _bands_for_page(bands_by_page, row.page))
            if hits and _assignable(hits):
                pair_hits[(i, r_i)] = hits
                pair_score[(i, r_i)] = (len(hits)
                                        + 0.01 * sum(h[0].score for h in hits.values()))

    assignment = _assign_rows_in_order(item_idxs, len(rows), pair_score)

    # an item's TERRITORY: the doc rows between its neighbors' assigned rows.
    # Receipt items span several printed lines (description line, EAN line,
    # numbers line) — cells missing from the assigned row live there (E-30b).
    assigned_sorted = sorted((r, i) for i, r in assignment.items() if r >= 0)
    territory: dict[int, tuple[int, int]] = {}
    for k, (r_i, i) in enumerate(assigned_sorted):
        # inner edges are neighbour-bounded; the OUTER edges get a small
        # fixed slack instead of the whole page — a receipt item spans a few
        # printed lines, but "everything above the first item" reaches the
        # letterhead (a phone-number fragment became a verified unit_price)
        lo = (assigned_sorted[k - 1][0] + 1 if k > 0
              else max(0, r_i - TERRITORY_EDGE_ROWS))
        hi = (assigned_sorted[k + 1][0] if k + 1 < len(assigned_sorted)
              else min(len(rows), r_i + TERRITORY_EDGE_ROWS + 1))
        territory[i] = (lo, hi)

    for i, obj in enumerate(row_objects):
        if not isinstance(obj, dict):
            continue
        r_i = assignment.get(i, -1)
        hits = pair_hits.get((i, r_i), {})
        for col, cspec in columns.items():
            flat = f"{name}[{i}].{col}"
            value = obj.get(col)
            # proof operands are row-scoped names; on a flattened spec they
            # would resolve against top-level fields — the row relation
            # already ran against the row object
            cell_spec = replace(cspec, name=flat, columns=None, proof=None)
            if value is None or (isinstance(value, str) and not value.strip()):
                results[flat] = FieldResult(name=flat, value=None,
                                            status=Status.NOT_PRESENT, confidence=1.0)
                continue
            if r_i < 0:
                results[flat] = FieldResult(
                    name=flat, value=value, status=Status.NOT_FOUND, confidence=0.0,
                    notes=["no document row matches this extracted line item"])
                continue
            row = rows[r_i]
            match = hits.get(col)
            if match is None:
                # E-31: wrapped cells (long descriptions) — retry on the row
                # merged with its neighbours
                match = _adjacent_retry(cell_spec, value, rows, r_i)
            if match is None:
                match = _territory_retry(cell_spec, value, rows,
                                         r_i, territory.get(i))
            if match is None:
                results[flat] = FieldResult(
                    name=flat, value=value, status=Status.NOT_FOUND, confidence=0.0,
                    notes=[f"cell not found in the matched item row (row text: "
                           f"{row.text[:60]!r})"])
                continue
            m, m_row = match
            bbox_px = m.bbox_px()
            if bbox_px is None:
                results[flat] = FieldResult(name=flat, value=value,
                                            status=Status.NOT_FOUND, confidence=0.0)
                continue
            w, h = page_sizes[m_row.page]
            norm = tuple(min(1.0, max(0.0, v)) for v in
                         (bbox_px[0] / w, bbox_px[1] / h, bbox_px[2] / w, bbox_px[3] / h))
            status = Status.VERIFIED if m.exact else Status.LOW_CONFIDENCE
            confidence = m.score if m.exact else m.score * 0.9
            notes = [] if m.exact else ["fuzzy match — human should glance"]
            if status == Status.VERIFIED and _is_short_number(value):
                band = (_bands_for_page(bands_by_page, m_row.page) or {}).get(col)
                if band is None:
                    # E-22 applies to cells too: a bare '1' with no column
                    # band to place it is a guess, not a proof
                    status = Status.LOW_CONFIDENCE
                    confidence = min(confidence, 0.6)
                    notes.append("short value with no column band — location "
                                 "is a guess, human should glance")
            results[flat] = FieldResult(
                name=flat, value=value, status=status,
                confidence=confidence,
                page=m_row.page, bbox=norm, evidence=m.matched_text,
                candidates=[Candidate(page=m_row.page, bbox=norm, score=m.score,
                                      evidence=m.matched_text, exact=m.exact)],
                notes=notes)

        _row_arithmetic(results, name, i, obj, columns)
    return results


def _assignable(hits: dict) -> bool:
    """A row claim needs real evidence: 2+ matched cells, or one exact match
    that isn't a bare short number. A lone '1' matching some stray digit must
    not drag the whole extraction row onto that doc row — located-but-wrong is
    worse than an honest not_found."""
    if len(hits) >= 2:
        return True
    (m, _row), = hits.values()
    from .canon import canon_value
    n = len(canon_value(m.matched_text))
    return (m.exact and n >= 3) or n >= 6


def _assign_rows_in_order(item_idxs: list[int], n_rows: int,
                          pair_score: dict[tuple[int, int], float]) -> dict[int, int]:
    """Extraction rows ↦ doc rows, one-to-one, maximizing total cell-hit score.

    Models emit line items in document order (and build_rows yields reading
    order), so the primary pass is an order-preserving alignment — the k-th
    copy of a duplicated product lands on the k-th matching document row.
    A rescue pass then greedily places any item the alignment left out (rows
    genuinely returned out of order), on whatever matching rows remain free.
    """
    n_i = len(item_idxs)
    dp = [[0.0] * (n_rows + 1) for _ in range(n_i + 1)]
    back = [[0] * (n_rows + 1) for _ in range(n_i + 1)]  # 0=skip row, 1=skip item, 2=match
    for a in range(n_i + 1):
        for r in range(n_rows + 1):
            if a == 0 and r == 0:
                continue
            # strict comparisons: on ties, skip-row wins, so equal-scoring
            # matches land on the EARLIEST possible doc row (stable behavior)
            best_s, best_m = float("-inf"), 0
            if r > 0:
                best_s, best_m = dp[a][r - 1], 0
            if a > 0 and dp[a - 1][r] > best_s:
                best_s, best_m = dp[a - 1][r], 1
            if a > 0 and r > 0:
                s = pair_score.get((item_idxs[a - 1], r - 1))
                if s is not None and dp[a - 1][r - 1] + s > best_s:
                    best_s, best_m = dp[a - 1][r - 1] + s, 2
            dp[a][r], back[a][r] = best_s, best_m
    out: dict[int, int] = {}
    a, r = n_i, n_rows
    while a > 0 or r > 0:
        move = back[a][r]
        if move == 2:
            out[item_idxs[a - 1]] = r - 1
            a, r = a - 1, r - 1
        elif move == 1:
            a -= 1
        else:
            r -= 1
    used = set(out.values())
    for i in item_idxs:
        if i in out:
            continue
        best_r, best_s = -1, 0.0
        for r_i in range(n_rows):
            s = pair_score.get((i, r_i))
            if s is not None and r_i not in used and s > best_s:
                best_r, best_s = r_i, s
        if best_r >= 0:
            out[i] = best_r
            used.add(best_r)
    return out


def _column_bands(columns: dict[str, FieldSpec], rows: list[Row]
                  ) -> dict[int, dict[str, tuple[float, float]]]:
    """Column x-bands from the table header row, per page (audit B1).

    The header is the row where the most distinct column labels appear (2+
    required — a lone label is any stray word); each found label's x-span
    becomes its column's band. qty=1 rows print unit_price == amount, and only
    WHERE a span sits can tell the twins apart."""
    canons = {col: anchors_for(col, cspec.anchors)
              for col, cspec in columns.items()}
    best: dict[int, tuple[tuple[int, float], dict[str, tuple[float, float]]]] = {}
    for row in rows:
        found: dict[str, tuple[float, float]] = {}
        for col, anchor_canons in canons.items():
            # leftmost label occurrence wins: headers can carry two price-ish
            # labels ("zákl. cena jedn. | cena za MU") and the canonical
            # column is the first in print order; longer label breaks a tie
            hit = min(((pos, -len(a), a) for a in anchor_canons
                       for pos in find_all(row.canon, a)), default=None)
            if hit is None:
                continue
            pos, _, a = hit
            t0 = row.canon_idx[pos]
            t1 = row.canon_idx[min(pos + len(a), len(row.canon)) - 1] + 1
            bb = row.char_range_bbox(t0, t1)
            if bb is not None:
                found[col] = (bb[0], bb[2])
        if len(found) >= 2:
            key = (len(found), -row.top)  # most labels, then topmost
            cur = best.get(row.page)
            if cur is None or key > cur[0]:
                best[row.page] = (key, found)
    return {page: bands for page, (_, bands) in best.items()}


def _bands_for_page(by_page: dict[int, dict], page: int) -> Optional[dict]:
    """Multi-page tables usually repeat the header; when a page has none,
    the nearest earlier header's x-geometry still applies."""
    if page in by_page:
        return by_page[page]
    earlier = [p for p in by_page if p < page]
    return by_page[max(earlier)] if earlier else None


def _row_cell_hits(obj: dict, columns: dict[str, FieldSpec], row: Row,
                   bands: Optional[dict[str, tuple[float, float]]] = None) -> dict:
    """Which cells of this extraction row match inside this document row.

    Two rules beyond per-cell best-match:
    - a span belongs to ONE cell (qty=1 twins must not stack on one print);
    - banded columns claim the matching span nearest their header's x-center,
      the rest resolve in schema order = printed order (left to right)."""
    per_col: dict[str, list] = {}
    for col, cspec in columns.items():
        value = obj.get(col)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        matches = _run_matcher(cspec, [row], value)
        if matches:
            per_col[col] = matches
    if not per_col:
        return {}

    hits: dict[str, tuple] = {}
    used: list[tuple[int, int]] = []

    def overlaps(m) -> bool:
        return any(not (m.end <= s or m.start >= e) for s, e in used)

    banded: list[tuple[float, str, Any]] = []
    for col, matches in per_col.items():
        band = (bands or {}).get(col)
        if band is None:
            continue
        band_cx = (band[0] + band[1]) / 2
        top_score = max(m.score for m in matches)
        for m in matches:
            if m.score < top_score:  # never trade match quality for position
                continue
            bb = row.char_range_bbox(m.start, m.end)
            if bb is not None:
                banded.append((abs((bb[0] + bb[2]) / 2 - band_cx), col, m))
    for _, col, m in sorted(banded, key=lambda t: t[0]):
        if col in hits or overlaps(m):
            continue
        hits[col] = (m, row)
        used.append((m.start, m.end))

    for col in columns:  # schema order = printed order for the rest
        if col not in per_col or col in hits:
            continue
        ranked = sorted(per_col[col], key=lambda m: (-m.score, m.start))
        pick = next((m for m in ranked if not overlaps(m)), None)
        if pick is None:  # every span taken — share rather than drop the cell
            pick = ranked[0]
        hits[col] = (pick, row)
        used.append((pick.start, pick.end))
    return hits


def _adjacent_retry(cspec: FieldSpec, value, rows: list[Row], r_i: int):
    # long glued values (multi-line descriptions with attribute lines) wrap
    # over several printed lines around the numbers row — widen the merge
    # window with the value's length, one row per ~40 chars, both directions
    span = 1 + min(4, len(str(value)) // 30)
    lo, hi = max(0, r_i - span), min(len(rows), r_i + 1 + span)
    group = [r for r in rows[lo:hi] if r.page == rows[r_i].page]
    if len(group) < 2:
        return None
    merged = merged_row(sorted(group, key=lambda r: r.top))
    matches = _run_matcher(cspec, [merged], value)
    if not matches and cspec.type == FieldType.TEXT:
        # wrapped description (E-31 hard case): the name's tail sits on the
        # next printed line with the row's numbers in between — a contiguous
        # window can't span that, the token-union BLOCK matcher can
        block_spec = FieldSpec(name=cspec.name, type=FieldType.BLOCK,
                               anchors=cspec.anchors)
        matches = _run_matcher(block_spec, [merged], value)
    if matches:
        best = max(matches, key=lambda m: m.score)
        return best, merged
    return None


def _territory_retry(cspec: FieldSpec, value, rows: list[Row], r_i: int,
                     bounds: Optional[tuple[int, int]]):
    """Search the item's own territory rows for a cell the assigned row and
    its immediate neighbours don't carry. Bare short values stay excluded —
    a lone '1' somewhere in the territory proves nothing (E-22)."""
    if bounds is None:
        return None
    from .canon import canon_value
    if len(canon_value(str(value))) < 3:
        return None
    lo, hi = bounds
    best = None
    for idx in range(lo, min(hi, len(rows))):
        if idx == r_i or rows[idx].page != rows[r_i].page:
            continue
        matches = _run_matcher(cspec, [rows[idx]], value)
        for m in matches:
            key = (m.exact, m.score, -idx)
            if best is None or key > best[0]:
                best = (key, m, rows[idx])
    if best is None:
        return None
    return best[1], best[2]


def _row_arithmetic(results: dict[str, FieldResult], name: str, i: int, obj: dict,
                    columns: dict[str, FieldSpec]) -> None:
    """Per-row relations from the column schema (§6.6.4) — the invoice's
    qty × unit_price ≈ amount comes from the name-hint layer, any other
    domain declares its own `proof` on a column."""
    from ..verify.arithmetic import evaluate_relation
    for col, cspec in columns.items():
        proof = cspec.proof
        if not proof:
            continue
        target = results.get(f"{name}[{i}].{col}")
        if target is None or target.value is None:
            continue
        located_cells = {c: obj.get(c) for c in columns
                         if (fr := results.get(f"{name}[{i}].{c}")) is not None
                         and fr.status not in (Status.NOT_FOUND, Status.NOT_PRESENT)}
        outcome = evaluate_relation(proof, located_cells.get(col), located_cells)
        if outcome is None:
            continue
        holds, equation = outcome
        operand_names = (proof.get("sum") or proof.get("product")
                         or proof.get("percent_of") or [])
        if holds:
            # the equation proves every participating cell, not just the target
            for c in (col, *operand_names):
                fr = results.get(f"{name}[{i}].{c}")
                if fr is not None and fr.value is not None:
                    fr.notes.append(f"arithmetic passed: {equation}")
            if target.proof is None:
                target.proof = "arithmetic"
        else:
            # a failing relation makes every participating cell suspect —
            # the wrong one is as likely an operand as the target
            for c in (col, *operand_names):
                fr = results.get(f"{name}[{i}].{c}")
                if fr is not None and fr.value is not None:
                    fr.notes.append(f"⚠ arithmetic: row relation fails ({equation})")
                    if fr.status == Status.VERIFIED:
                        fr.notes.append("value located but the row math disagrees")
