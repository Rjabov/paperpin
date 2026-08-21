"""Label anchors — disambiguation (§6.5, the qty=1 fix).

Anchors are label words expected near a field's value (same visual row, or the
row directly above). They come exclusively from the SCHEMA (declared or
enriched by name hints in schemas.py) — the core stays domain-free.
"""
from __future__ import annotations

from typing import Optional

from .canon import canon_value, find_all
from .matchers import RawMatch
from .rows import Row

SAME_ROW_BONUS = 0.35
ROW_ABOVE_BONUS = 0.25
AMBIGUITY_EPSILON = 0.05


def anchors_for(field_name: str, extra: list[str]) -> list[str]:
    """Canonical anchor list — purely what the schema DECLARES. Name-based
    guessing lives in one place (schemas.enrich_spec), never in the core."""
    seen: set[str] = set()
    out: list[str] = []
    for a in extra or []:
        c = canon_value(a)
        # single-character anchors ("n." → "n") match almost any row — junk
        if len(c) >= 2 and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _row_above(match_row: Row, rows: list[Row]) -> Optional[Row]:
    height = match_row.bottom - match_row.top
    best, best_gap = None, None
    for r in rows:
        if r.page != match_row.page or r is match_row:
            continue
        gap = match_row.top - r.bottom
        if gap < -height * 0.3 or gap > max(height, 8.0) * 2.2:
            continue
        # needs horizontal relevance to the match's column band
        inter = min(r.x1, match_row.x1) - max(r.x0, match_row.x0)
        if inter <= 0:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = r, gap
    return best


def _find_anchor(row: Row, anchor_canons: list[str],
                 exclude: Optional[tuple[int, int]] = None) -> Optional[str]:
    """Longest anchor found in the row, outside the excluded value range.

    Short anchors (canon <= 4) must sit on word boundaries in the SOURCE
    text: 'Dodavatel' contains 'vat' and 'Splatnost' contains 'no', and a
    raw substring hit endorses whatever number shares the row — while also
    disarming the E-22 short-number guard. Longer anchors stay substrings
    so compound labels keep working ('Rechnungsbetrag' carries 'betrag')."""
    best: Optional[str] = None
    for a in sorted(anchor_canons, key=len, reverse=True):
        for pos in find_all(row.canon, a):
            t_start = row.canon_idx[pos]
            t_end = row.canon_idx[min(pos + len(a), len(row.canon)) - 1] + 1
            if exclude is not None:
                if not (t_end <= exclude[0] or t_start >= exclude[1]):
                    continue
            if len(a) <= 4:
                left_ok = t_start == 0 or not row.text[t_start - 1].isalnum()
                right_ok = (t_end >= len(row.text)
                            or not row.text[t_end].isalnum())
                if not (left_ok and right_ok):
                    continue
            best = a
            break
        if best:
            break
    return best


def score_anchor(match: RawMatch, rows: list[Row], anchor_canons: list[str]
                 ) -> tuple[float, Optional[str]]:
    if not anchor_canons:
        return 0.0, None
    found = _find_anchor(match.row, anchor_canons, exclude=(match.start, match.end))
    if found:
        return SAME_ROW_BONUS, found
    above = _row_above(match.row, rows)
    if above is not None:
        found = _find_anchor(above, anchor_canons)
        if found:
            return ROW_ABOVE_BONUS, found
    return 0.0, None
