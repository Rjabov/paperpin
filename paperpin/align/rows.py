"""Visual rows (§6.3): true printed lines, formed docTR-style.

Segments are deskewed by the median of their own quad baseline angles, then
grouped by y-center against the page's median segment height with a FIXED
per-line reference — the band never grows, so dense print cannot chain-merge
(the METRO regression: 51 printed lines fused into 24 mega-rows). Wide
horizontal gaps additionally split each line into column cells, emitted as
extra matchable rows flagged `is_cell` (two-column smear fix); the full line
row is always kept.

Sub-box precision (§6.4): the text layer supplies per-char boxes (exact);
OCR segments fall back to proportional slicing — acceptable, upgraded later.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ..types import Segment

COLUMN_BAND_OVERLAP = 0.5  # x-overlap ratio for cross-row merging (E-15)
MAX_MERGE_ROWS = 3
MIN_SKEW_RAD = math.radians(0.5)   # below this, treat the page as straight
MAX_SKEW_RAD = math.radians(15.0)  # above this, distrust the estimate


@dataclass
class Row:
    page: int
    segments: list[Segment]
    text: str = ""
    # char_src[i] = (segment_index_in_row, char_index_in_segment) or None for
    # the injected single spaces between segments
    char_src: list[Optional[tuple[int, int]]] = field(default_factory=list)
    canon: str = ""
    canon_idx: list[int] = field(default_factory=list)  # canon pos -> text pos
    is_cell: bool = False  # column cell cut from a full line (extra view)

    @property
    def top(self) -> float:
        return min(s.top for s in self.segments)

    @property
    def bottom(self) -> float:
        return max(s.bottom for s in self.segments)

    @property
    def x0(self) -> float:
        return min(s.x0 for s in self.segments)

    @property
    def x1(self) -> float:
        return max(s.x1 for s in self.segments)

    def char_range_bbox(self, start: int, end: int
                        ) -> Optional[tuple[float, float, float, float]]:
        """Pixel bbox of text[start:end] — union of per-segment slices."""
        by_seg: dict[int, list[int]] = {}
        for i in range(start, min(end, len(self.char_src))):
            src = self.char_src[i]
            if src is None:
                continue
            by_seg.setdefault(src[0], []).append(src[1])
        if not by_seg:
            return None
        boxes = []
        for seg_i, char_idxs in by_seg.items():
            seg = self.segments[seg_i]
            lo, hi = min(char_idxs), max(char_idxs) + 1
            boxes.append(_slice_segment(seg, lo, hi))
        x0 = min(b[0] for b in boxes)
        top = min(b[1] for b in boxes)
        x1 = max(b[2] for b in boxes)
        bottom = max(b[3] for b in boxes)
        return (x0, top, x1, bottom)


def _slice_segment(seg: Segment, lo: int, hi: int) -> tuple[float, float, float, float]:
    n = len(seg.text)
    lo = max(0, min(lo, n))
    hi = max(lo, min(hi, n))
    if seg.char_boxes and len(seg.char_boxes) == n:
        boxes = seg.char_boxes[lo:hi]
        if boxes:
            # CTC-derived char boxes can start inside the det box and shave
            # edge glyphs (a thin leading '1' loses half its width) — when the
            # slice touches a segment edge, the det bound is the truth there
            x0 = seg.x0 if lo == 0 else min(b[0] for b in boxes)
            x1 = seg.x1 if hi == n else max(b[2] for b in boxes)
            return (x0, min(b[1] for b in boxes),
                    x1, max(b[3] for b in boxes))
    # weighted proportional slice (§6.4) — same glyph model as segmentize
    if n == 0:
        return (seg.x0, seg.top, seg.x1, seg.bottom)
    from ..geometry.segmentize import _char_edges
    edges = _char_edges(seg.text, seg.x0, seg.x1)
    return (edges[lo], seg.top, edges[hi], seg.bottom)


def _baseline_angle(s: Segment, to_proc) -> Optional[float]:
    """Angle of the segment's own baseline (bottom quad edge) in the
    processed (upright) frame, radians."""
    if not s.quad or len(s.quad) != 4:
        return None
    quad = [to_proc(x, y) for x, y in s.quad]
    pts = sorted(quad, key=lambda p: p[1])
    (x1, y1), (x2, y2) = sorted(pts[2:], key=lambda p: p[0])
    if x2 - x1 < 2.0:
        return None
    return math.atan2(y2 - y1, x2 - x1)


def _page_skew(segs: list[Segment], to_proc) -> float:
    """Median of per-segment baseline angles — immune to the layout artifacts
    that poison a global y-on-x regression (columns, totals blocks)."""
    angles = sorted(a for a in (_baseline_angle(s, to_proc) for s in segs)
                    if a is not None)
    if len(angles) < 3:
        return 0.0
    skew = angles[len(angles) // 2]
    if abs(skew) < MIN_SKEW_RAD or abs(skew) > MAX_SKEW_RAD:
        return 0.0
    return skew


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0.0


def _to_processed(k: int, size: tuple[float, float]):
    """Point mapper original→processed for k ccw quarter-turns (the frame the
    orientation search OCR'd in, where text runs horizontally). Mirrors
    geometry.transform.rotate90 exactly."""
    w, h = size
    if k == 1:
        return lambda x, y: (y, w - x)
    if k == 2:
        return lambda x, y: (w - x, h - y)
    if k == 3:
        return lambda x, y: (h - y, x)
    return lambda x, y: (x, y)


def build_rows(segments: list[Segment],
               orientations: Optional[dict[int, int]] = None,
               page_sizes: Optional[dict[int, tuple[float, float]]] = None
               ) -> list[Row]:
    from .canon import canonical_map

    rows: list[Row] = []
    by_page: dict[int, list[Segment]] = {}
    for s in segments:
        by_page.setdefault(s.page, []).append(s)

    for page, segs in sorted(by_page.items()):
        k = (orientations or {}).get(page, 0) % 4
        size = (page_sizes or {}).get(page)
        if k and size is None:  # cannot normalize without the page size
            k = 0
        to_proc = _to_processed(k, size or (0.0, 0.0))

        # every grouping decision happens in the PROCESSED (upright) frame;
        # emitted rows keep the original-space segments untouched
        boxes: dict[int, tuple[float, float, float, float]] = {}
        for i, s in enumerate(segs):
            (ax, ay), (bx, by) = to_proc(s.x0, s.top), to_proc(s.x1, s.bottom)
            boxes[i] = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))

        skew = _page_skew(segs, to_proc)
        cos_t, sin_t = math.cos(skew), math.sin(skew)

        def yc(i: int) -> float:
            x0, y0, x1, y1 = boxes[i]
            return cos_t * (y0 + y1) / 2.0 - sin_t * (x0 + x1) / 2.0

        y_med = _median([boxes[i][3] - boxes[i][1] for i in boxes])
        tol = max(2.0, y_med / 2.0)

        # lines: seed-anchored grouping on deskewed centers — the reference
        # is the FIRST member, so a tall stray box cannot grow the band and
        # swallow the following lines
        lines: list[list[int]] = []
        seed_yc = 0.0
        for i in sorted(boxes, key=yc):
            c = yc(i)
            if not lines or abs(c - seed_yc) > tol:
                lines.append([i])
                seed_yc = c
            else:
                lines[-1].append(i)

        # adaptive column break from this page's gap statistics (docTR-style)
        gaps: list[float] = []
        for line in lines:
            xs = sorted(line, key=lambda i: boxes[i][0])
            gaps.extend(g for a, b in zip(xs, xs[1:])
                        if (g := boxes[b][0] - boxes[a][2]) > 0)
        width = (max((boxes[i][2] for i in boxes), default=0.0)
                 - min((boxes[i][0] for i in boxes), default=0.0))
        if len(gaps) >= 3:
            break_dist = max(3.0 * _median(gaps), y_med)
        else:
            break_dist = max(3.0 * y_med, 0.04 * width)

        for line in lines:
            line.sort(key=lambda i: boxes[i][0])
            rows.append(_make_row(page, [segs[i] for i in line], canonical_map))
            # column cells: emit only when the line actually splits
            cells: list[list[int]] = [[line[0]]]
            for prev, nxt in zip(line, line[1:]):
                if boxes[nxt][0] - boxes[prev][2] > break_dist:
                    cells.append([])
                cells[-1].append(nxt)
            if len(cells) > 1:
                for cell in cells:
                    cell_row = _make_row(page, [segs[i] for i in cell], canonical_map)
                    cell_row.is_cell = True
                    rows.append(cell_row)

    rows.sort(key=lambda r: (r.page, r.top, r.x0, r.is_cell))
    return rows


def _make_row(page: int, cluster: list[Segment], canonical_map) -> Row:
    row = Row(page=page, segments=cluster)
    parts: list[str] = []
    char_src: list[Optional[tuple[int, int]]] = []
    for i, seg in enumerate(cluster):
        if i > 0:
            parts.append(" ")
            char_src.append(None)
        parts.append(seg.text)
        char_src.extend((i, j) for j in range(len(seg.text)))
    row.text = "".join(parts)
    row.char_src = char_src
    row.canon, row.canon_idx = canonical_map(row.text)
    return row


def merged_row(rows_subset: list[Row], joiner: str = " ") -> Row:
    """Cross-row merge (E-15): virtual row concatenating vertically adjacent
    rows, preserving source maps, for values wrapped across lines."""
    from .canon import canonical_map

    merged = Row(page=rows_subset[0].page, segments=[])
    parts: list[str] = []
    char_src: list[Optional[tuple[int, int]]] = []
    seg_offset = 0
    for r_i, r in enumerate(rows_subset):
        if r_i > 0:
            parts.append(joiner)
            char_src.extend([None] * len(joiner))
        parts.append(r.text)
        for src in r.char_src:
            char_src.append(None if src is None else (src[0] + seg_offset, src[1]))
        merged.segments.extend(r.segments)
        seg_offset += len(r.segments)
    merged.text = "".join(parts)
    merged.char_src = char_src
    merged.canon, merged.canon_idx = canonical_map(merged.text)
    return merged


def cross_row_candidates(rows: list[Row]) -> list[Row]:
    """Vertically-adjacent row groups within a column band (E-15), capped at
    MAX_MERGE_ROWS. Used when single-row matching almost-hits.

    Column cells chain too: a full two-column line x-overlaps BOTH columns, so
    a left-column line between two address lines poisons the full-row chain —
    the right-column cell chain is the clean unit (corpus regression)."""
    out: list[Row] = []
    per_page: dict[int, list[Row]] = {}
    for r in rows:
        per_page.setdefault(r.page, []).append(r)
    for page_rows in per_page.values():
        page_rows = sorted(page_rows, key=lambda r: (r.top, r.x0))
        for i, r in enumerate(page_rows):
            group = [r]
            for nxt in page_rows[i + 1:]:
                prev = group[-1]
                height = max(prev.bottom - prev.top, 8.0)
                gap = nxt.top - prev.bottom
                if gap > height * 1.6:
                    break
                if nxt.top < prev.top + height * 0.3:  # same printed line
                    continue                           # (a cell of it) — not a next line
                if _x_overlap_ratio(prev, nxt) < COLUMN_BAND_OVERLAP:
                    continue
                group.append(nxt)
                out.append(merged_row(list(group)))
                if len(group) >= MAX_MERGE_ROWS:
                    break
    return out


def _x_overlap_ratio(a: Row, b: Row) -> float:
    inter = min(a.x1, b.x1) - max(a.x0, b.x0)
    if inter <= 0:
        return 0.0
    return inter / max(1e-6, min(a.x1 - a.x0, b.x1 - b.x0))
