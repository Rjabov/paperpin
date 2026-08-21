"""GEOMETRY stage (§6.1): produce Segments per page in upright-original space.

- textlayer route: exact word+char boxes from the PDF text layer (PDF points).
- ocr route: best-of-4 orientation search on a thumbnail (E-1: scored by OCR
  confidence), full OCR at the winning rotation, optional small-text rescue
  pass (E-4: never blanket-upscale — upscaling is only tried when the first
  pass reads little and text is measured small, and it must WIN on score to
  be kept), then every box is mapped back through the inverse transform chain.

Results are cached by document sha256 (§6.7) so re-runs with a new prompt or
schema never re-OCR.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PIL import Image

from .. import cache as segcache
from ..backends.base import OcrBackend
from ..intake.loader import MAX_RASTER_LONG_SIDE, Page
from ..types import Segment
from .transform import TransformChain, rotate90, scale

THUMB_SIDE = 1000
OCR_RENDER_DPI = 220        # raster resolution for scanned PDF pages
RESCUE_MEDIAN_HEIGHT = 14.0  # px; below this the rescue upscale pass is considered
RESCUE_SCALE = 2.0
# hard ceiling for anything handed to the OCR engine; the killing edge is
# the det stage's own /32 rounding — a near-unity cv2 shrink that segfaults
# on big rasters (measured 2026-08-20: ratio >~0.996 at >~13.8MP dies,
# identity and grows survive) — so _det_safe also snaps dimensions to /32,
# and the rescue upscale must not smuggle an oversized image past the cap
MAX_OCR_PIXELS = 14_000_000
# the det preprocessor upscales the SHORT side to this before inference
# (rapidocr config: limit_side_len 736, limit_type min) — a thin wide strip
# balloons far past its own pixel count inside the engine, so every crop
# path must budget against the PROJECTED size, not the input size
DET_MIN_SIDE = 736


def det_projected_pixels(w: float, h: float) -> int:
    """Pixels the det model will actually allocate for a w x h input."""
    m = min(w, h)
    if m <= 0:
        return 0
    if m >= DET_MIN_SIDE:
        return int(w * h)
    return int(w * h * (DET_MIN_SIDE / m) ** 2)


@dataclass
class PageSegments:
    segments: list[Segment]          # coords in upright-original page space
    route: str
    orientation_k: int = 0           # applied ccw 90° rotations during OCR
    ocr_size: Optional[tuple[float, float]] = None  # raster size OCR actually ran on
    meta: Optional[dict] = None


def segmentize(page: Page, backend: Optional[OcrBackend], doc_sha: str,
               use_cache: bool = True) -> PageSegments:
    if page.route == "textlayer":
        from ..backends import textlayer
        if page.text_segments is None:
            page.text_segments = textlayer.extract_segments(page.pdf_page)
        for s in page.text_segments:
            s.page = page.index
        return PageSegments(segments=page.text_segments, route="textlayer")

    if backend is None:
        raise ValueError("page requires OCR but no OCR backend is available")

    # the cache key must change whenever raster geometry changes — the
    # constants are baked in so nobody has to remember to bump a version
    variant = (f"{backend.name}_v8_r{int(MAX_RASTER_LONG_SIDE)}"
               f"d{int(OCR_RENDER_DPI)}")
    if use_cache:
        hit = segcache.load_segments(doc_sha, page.index, backend.name, variant)
        if hit is not None:
            segs, meta = hit
            return PageSegments(segments=segs, route="ocr",
                                orientation_k=meta.get("orientation_k", 0),
                                ocr_size=tuple(meta["ocr_size"]) if meta.get("ocr_size") else None,
                                meta={**meta, "cache_hit": True})

    img = page.raster(dpi=OCR_RENDER_DPI if page.pdf_page is not None else None)
    best_k = _best_orientation(img, backend)
    # 0-vs-180 disambiguation: recognition output scores identically for a
    # page and its 180° flip (the rec stage un-flips each line internally,
    # so text and confidence look fine either way) — but then every per-char
    # box is built in a reversed frame: silent wrong sub-boxes. The angle
    # classifier is the purpose-built discriminator; ask it before committing.
    probe = getattr(backend, "flipped_majority", None)
    if probe is not None:
        thumb = img.copy()
        thumb.thumbnail((900, 900))
        t = thumb if best_k == 0 else thumb.rotate(90 * best_k, expand=True)
        if probe(t):
            best_k = (best_k + 2) % 4
    work = img if best_k == 0 else img.rotate(90 * best_k, expand=True)

    work, pre_sx, pre_sy = _det_safe(work)
    segments = backend.recognize(work)
    score = _score(segments)

    # Rescue pass (E-4): only when the plain read is weak AND text measures small.
    heights = sorted(s.height for s in segments) if segments else []
    median_h = heights[len(heights) // 2] if heights else 0.0
    used_sx = used_sy = 1.0
    if (not segments) or (median_h and median_h < RESCUE_MEDIAN_HEIGHT):
        budget = (MAX_OCR_PIXELS / (work.width * work.height)) ** 0.5
        rescue_scale = min(RESCUE_SCALE, budget)
        if rescue_scale > 1.05:  # a barely-bigger image cannot rescue anything
            # snap to /32 so the det stage's own resize is an identity — its
            # near-unity cv2 shrink is the measured segfault (see _det_safe)
            uw = _snap32(int(work.width * rescue_scale))
            uh = _snap32(int(work.height * rescue_scale))
            up = work.resize((uw, uh), Image.LANCZOS)
            rescue = backend.recognize(up)
            if _score(rescue) > score * 1.05:  # must clearly win to be kept
                segments = rescue
                used_sx, used_sy = uw / work.width, uh / work.height

    # Map processed-space boxes back to upright-original space.
    chain = TransformChain((img.width, img.height))
    if best_k:
        chain.push(rotate90(best_k, (img.width, img.height)))
    if (pre_sx, pre_sy) != (1.0, 1.0):
        chain.push(scale(pre_sx, pre_sy, chain.processed_size))
    if (used_sx, used_sy) != (1.0, 1.0):
        chain.push(scale(used_sx, used_sy, chain.processed_size))

    for s in segments:
        # Proportional per-char boxes are built in PROCESSED space — where the
        # text is guaranteed horizontal — and each one is mapped back through
        # the chain. Sub-box slicing downstream then works for any rotation
        # (the §6.2 trap: slicing original-space boxes breaks on rotated pages).
        n = len(s.text)
        if n > 0 and s.char_boxes is None:
            edges = _char_edges(s.text, s.x0, s.x1)
            s.char_boxes = [(edges[i], s.top, edges[i + 1], s.bottom)
                            for i in range(n)]
        if s.char_boxes:
            # backend-supplied (CTC) and proportional boxes alike are built in
            # processed space — every one maps through the chain
            s.char_boxes = [chain.map_bbox_to_original(cb) for cb in s.char_boxes]
        s.x0, s.top, s.x1, s.bottom = chain.map_bbox_to_original((s.x0, s.top, s.x1, s.bottom))
        if s.quad:
            s.quad = chain.map_points_to_original(s.quad)
        s.page = page.index

    # OCR may have run on a working raster that differs from the page's
    # original space (PDF points, or a downscaled oversized camera image) —
    # express boxes in original space via uniform scale.
    if (page.size[0], page.size[1]) != (float(img.width), float(img.height)):
        sx = page.size[0] / img.width
        sy = page.size[1] / img.height
        for s in segments:
            s.x0 *= sx; s.x1 *= sx; s.top *= sy; s.bottom *= sy
            if s.quad:
                s.quad = [(x * sx, y * sy) for x, y in s.quad]
            if s.char_boxes:
                s.char_boxes = [(a * sx, b * sy, c * sx, d * sy)
                                for a, b, c, d in s.char_boxes]

    meta = {"orientation_k": best_k, "scale": used_sx,
            "ocr_size": [img.width, img.height], "backend": backend.name}
    if use_cache:
        segcache.save_segments(doc_sha, page.index, backend.name, variant, segments, meta)
    return PageSegments(segments=segments, route="ocr", orientation_k=best_k,
                        ocr_size=(img.width, img.height), meta=meta)


_NARROW = set(".,:;!i|l'`ıíì()[]{} ")
_WIDE = set("mwMW@")


def _glyph_weight(ch: str) -> float:
    if ch in _NARROW:
        return 0.45
    if ch in _WIDE:
        return 1.45
    if ch.islower():
        return 0.85
    return 1.0  # digits, uppercase, everything else


def _char_edges(text: str, x0: float, x1: float) -> list[float]:
    """Cumulative x-edges for proportional slicing, weighted by rough glyph
    widths — uniform slicing drifts badly on mixed label+value tokens like
    'CompanyID:32635093' (§6.4)."""
    weights = [_glyph_weight(c) for c in text]
    total = sum(weights) or 1.0
    span = x1 - x0
    edges = [x0]
    acc = 0.0
    for w in weights:
        acc += w
        edges.append(x0 + span * acc / total)
    return edges


def _score(segments: list[Segment]) -> float:
    return sum(s.conf * len(s.text.strip()) for s in segments)


ORIENTATION_MARGIN = 1.15  # a rotation must beat upright by ≥15% to be chosen


def _score_horizontal(segments: list[Segment]) -> float:
    """Confidence-weighted characters read in HORIZONTAL segments only.

    The raw Σ conf·len score barely separates orientations — the detector
    reads sideways text as vertical lines almost as confidently. But a
    correctly-oriented page yields WIDE segments while a mis-oriented one
    yields tall ones, so counting only width>height segments separates the
    orientations decisively in both directions.
    """
    return sum(s.conf * len(s.text.strip())
               for s in segments
               if s.width > s.height * 1.05 or len(s.text.strip()) <= 2)


def _snap32(v: int) -> int:
    return max(32, (v // 32) * 32)


def _det_safe(image: Image.Image) -> tuple[Image.Image, float, float]:
    """Every image handed to backend.recognize() goes through here; it
    enforces two engine facts the long-side raster cap alone does not:

    - the det budget: a near-square page passes the cap at >=14MP raw and a
      thin strip balloons via the internal short-side upscale (projection ~
      aspect ratio — uniform downscale cannot shrink it, so strips are
      PADDED to the det floor: white margin, no segments, coords valid);
    - the det stage rounds its input to /32 with a cv2 near-unity shrink
      that SEGFAULTS on big rasters (measured: macOS arm64, shrink ratio
      >~0.996 at >~13.8MP dies; identity and grows survive). Dimensions
      leaving here are multiples of 32, making that resize an identity.

    Returns (image, sx, sy) for the coordinate chain (1.0 for padding)."""
    w, h = image.width, image.height
    if (det_projected_pixels(w, h) > MAX_OCR_PIXELS
            and min(w, h) < DET_MIN_SIDE):
        pw = max(-(-w // 32) * 32, DET_MIN_SIDE)
        ph = max(-(-h // 32) * 32, DET_MIN_SIDE)
        padded = Image.new("RGB", (pw, ph), (255, 255, 255))
        padded.paste(image, (0, 0))
        return padded, 1.0, 1.0
    tw, th = w, h
    if det_projected_pixels(w, h) > MAX_OCR_PIXELS:
        s = (MAX_OCR_PIXELS / (w * h)) ** 0.5
        tw, th = int(w * s), int(h * s)
    tw, th = _snap32(tw), _snap32(th)
    if (tw, th) == (w, h):
        return image, 1.0, 1.0
    return (image.resize((tw, th), Image.LANCZOS), tw / w, th / h)


def _best_orientation(img: Image.Image, backend: OcrBackend) -> int:
    """E-1: score all four rotations on a thumbnail; EXIF already applied
    upstream, this catches physically-rotated content (sideways photos,
    rotated scans). Ties break toward upright (k=0) — a wrong rotation
    silently degrades every downstream sub-box."""
    thumb = img.copy()
    thumb.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.BILINEAR)
    scores = []
    for k in range(4):
        rotated = thumb if k == 0 else thumb.rotate(90 * k, expand=True)
        # a strip's thumbnail keeps its extreme aspect — the probe must not
        # be the call that blows the det budget
        rotated, _, _ = _det_safe(rotated)
        try:
            score = _score_horizontal(backend.recognize(rotated))
        except Exception:
            score = -1.0
        scores.append(score)
    best_k = max(range(4), key=lambda k: scores[k])
    if best_k != 0 and scores[best_k] < scores[0] * ORIENTATION_MARGIN:
        return 0
    return best_k
