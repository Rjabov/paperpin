"""Targeted high-resolution re-OCR for not_found fields (§DEEP-2).

OCR often reads a page's machine print but misses a damaged, small, or
low-contrast value region — while the model quote tells us WHAT text sits
there (text, never coordinates). For a not_found field: find an anchor row
(quote-context tokens, a partial print of the value itself, or the field's
label lexicon), re-OCR that row's neighborhood at high resolution, and run
the ordinary matcher over the recovered text. A hit pins as low_confidence —
recovered, still honest, never verified.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Callable, Optional

from PIL import Image

from ..align.anchors import anchors_for
from ..align.canon import canon_value
from ..align.rows import Row, build_rows
from ..types import FieldResult, FieldSpec, Segment, Status

RESCUE_UPSCALE = 3
RESCUE_MAX_PIXELS = 8_000_000  # area budget for one neighborhood re-read
MAX_RESCUES_PER_PAGE = 6
NEIGHBORHOOD_BELOW = 7.0   # row heights — wrapped values run downward
NEIGHBORHOOD_RIGHT = 0.5   # page widths — values sit right of their labels


def rescue_not_founds(results: dict[str, FieldResult],
                      specs: dict[str, FieldSpec],
                      rows: list[Row],
                      page_sizes: dict[int, tuple[float, float]],
                      route_by_page: dict[int, str],
                      page_image_provider: Callable[[int], Image.Image],
                      backend) -> int:
    """Attempt recovery for not_found fields; returns how many were rescued."""
    from ..align.aligner import _run_matcher
    budget: Counter = Counter()
    rescued = 0
    for name, fr in results.items():
        if fr.status != Status.NOT_FOUND or fr.value is None:
            continue
        spec = specs.get(name) or FieldSpec(name=name)
        for anchor_row in _anchor_rows(fr, spec, rows)[:2]:
            page = anchor_row.page
            if route_by_page.get(page) != "ocr" or budget[page] >= MAX_RESCUES_PER_PAGE:
                continue
            budget[page] += 1
            segments = _reocr_neighborhood(page_image_provider(page), anchor_row,
                                           page_sizes[page], backend)
            if not segments:
                continue
            matches = _run_matcher(spec, build_rows(segments), fr.value)
            if not matches:
                continue
            best = max(matches, key=lambda m: m.score)
            bbox_px = best.row.char_range_bbox(best.start, best.end)
            if bbox_px is None:
                continue
            w, h = page_sizes[page]
            fr.bbox = tuple(min(1.0, max(0.0, v)) for v in
                            (bbox_px[0] / w, bbox_px[1] / h,
                             bbox_px[2] / w, bbox_px[3] / h))
            fr.page = page
            fr.evidence = best.matched_text
            fr.status = Status.LOW_CONFIDENCE
            fr.confidence = min(0.6, best.score * 0.7)
            fr.notes.append("recovered by a targeted high-resolution re-read "
                            "of the region — human should glance")
            rescued += 1
            break
    return rescued


def _anchor_rows(fr: FieldResult, spec: FieldSpec, rows: list[Row]) -> list[Row]:
    """Rows likely NEXT TO the unread value, best first.

    Signals: quote-context tokens (the label words the model saw around the
    value), a partial print of the value itself (first line of an address
    that half-survived OCR), and the field's label lexicon."""
    value_canon = canon_value(str(fr.value))
    tokens: set[str] = set()
    if fr.quote:
        for t in re.split(r"\s+", str(fr.quote)):
            c = canon_value(t)
            if len(c) >= 3 and c not in value_canon:
                tokens.add(c)
    for t in re.split(r"\s+", str(fr.value)):
        c = canon_value(t)
        if len(c) >= 4:
            tokens.add(c)
    tokens.update(a for a in anchors_for(fr.name, spec.anchors) if len(a) >= 3)
    if not tokens:
        return []
    scored = []
    for row in rows:
        if row.is_cell:
            continue
        hits = sum(1 for t in tokens if t in row.canon)
        if hits:
            scored.append((hits, row.page, row.top, row))
    scored.sort(key=lambda s: (-s[0], s[1], s[2]))
    return [s[3] for s in scored]


def _reocr_neighborhood(page_image: Image.Image, anchor_row: Row,
                        page_size: tuple[float, float], backend
                        ) -> Optional[list[Segment]]:
    """Re-OCR the anchor row's neighborhood at high res; segments come back in
    ORIGINAL page coordinates so the ordinary row/bbox machinery applies."""
    page_w, page_h = page_size
    raster_w, raster_h = page_image.size
    sx, sy = raster_w / page_w, raster_h / page_h
    row_h = max(anchor_row.bottom - anchor_row.top, 6.0)
    x0 = max(0.0, anchor_row.x0 - 2 * row_h)
    x1 = min(page_w, max(anchor_row.x1, anchor_row.x0 + NEIGHBORHOOD_RIGHT * page_w))
    y0 = max(0.0, anchor_row.top - 1.5 * row_h)
    y1 = min(page_h, anchor_row.bottom + NEIGHBORHOOD_BELOW * row_h)
    box = (int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy))
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        return None
    crop = page_image.crop(box)
    # two ceilings guard the re-OCR. First: det upscales the SHORT side to
    # DET_MIN_SIDE, so a thin full-width strip projects far past its own
    # pixel count inside the engine; while the upscaled short side stays
    # under DET_MIN_SIDE that projection is invariant in the upscale, so the
    # long side must be trimmed, not the scale. Second: a hard area budget —
    # rescue is a peek at a neighborhood, never a full-page re-read, and on
    # photos with tall rows the neighborhood approaches the whole page.
    from ..geometry.segmentize import DET_MIN_SIDE
    short = min(crop.size)
    if short * RESCUE_UPSCALE < DET_MIN_SIDE:
        allowed_long = int(RESCUE_MAX_PIXELS * short / DET_MIN_SIDE ** 2)
        if max(crop.size) > allowed_long:
            if crop.width >= crop.height:  # keep the anchor-row end of the strip
                crop = crop.crop((0, 0, allowed_long, crop.height))
                box = (box[0], box[1], box[0] + allowed_long, box[3])
            else:
                crop = crop.crop((0, 0, crop.width, allowed_long))
                box = (box[0], box[1], box[2], box[1] + allowed_long)
    # scale (up OR down) into the budget — a downscaled high-res photo
    # neighborhood still reads better than no rescue at all
    upscale = min(float(RESCUE_UPSCALE),
                  (RESCUE_MAX_PIXELS / (crop.width * crop.height)) ** 0.5)
    if abs(upscale - 1.0) > 0.01:
        crop = crop.resize((max(1, int(crop.width * upscale)),
                            max(1, int(crop.height * upscale))), Image.LANCZOS)
    # every image the engine sees goes through the det gate: it budgets the
    # projection AND snaps dimensions to /32 so det's own rounding resize is
    # an identity (a near-unity shrink segfaults cv2 — see segmentize)
    from ..geometry.segmentize import _det_safe
    crop, dsx, dsy = _det_safe(crop)
    up_x, up_y = upscale * dsx, upscale * dsy
    try:
        raw = backend.recognize(crop)
    except Exception:
        return None
    out: list[Segment] = []
    for s in raw or []:
        out.append(Segment(
            text=s.text,
            x0=(box[0] + s.x0 / up_x) / sx,
            top=(box[1] + s.top / up_y) / sy,
            x1=(box[0] + s.x1 / up_x) / sx,
            bottom=(box[1] + s.bottom / up_y) / sy,
            conf=s.conf, page=anchor_row.page))
    return out
