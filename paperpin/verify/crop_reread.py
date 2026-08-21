"""Crop re-read (§6.6.2) — a genuinely second opinion on the pixels.

The pinned crop (padded) is re-OCR'd and compared against the evidence the
aligner matched. A box that drifted onto the label or a neighboring value
fails the re-read and the field is downgraded — this is the defense that
makes proportional sub-box slicing safe to trust (E-12/E-33).

Cheap on CPU: one small crop per verified field, OCR-routed pages only.
"""
from __future__ import annotations

from typing import Optional

from PIL import Image

from ..align.canon import canon_value
from ..align.matchers import value_number_set
from ..types import FieldSpec, FieldType

PAD_FRACTION = 0.45   # of crop height, each side
MIN_CROP_H = 14       # px; below this the crop is upscaled before OCR


def reread_crop(page_image: Image.Image, norm_bbox: tuple[float, float, float, float],
                backend, single_line: bool = False) -> Optional[str]:
    """OCR the padded crop; returns joined text or None when unreadable.

    single_line=True routes through the backend's rec-only line reader —
    right for short numeric crops (25× cheaper), wrong for wide text/id
    crops, which the 320px rec input would squash into garbage."""
    W, H = page_image.size
    x0, y0, x1, y1 = (norm_bbox[0] * W, norm_bbox[1] * H,
                      norm_bbox[2] * W, norm_bbox[3] * H)
    pad = max(3.0, (y1 - y0) * PAD_FRACTION)
    box = (int(max(0, x0 - pad)), int(max(0, y0 - pad)),
           int(min(W, x1 + pad)), int(min(H, y1 + pad)))
    if box[2] - box[0] < 4 or box[3] - box[1] < 4:
        return None
    crop = page_image.crop(box)
    # wide text crops re-OCR'd with detection hit det's short-side upscale
    # (see segmentize.det_projected_pixels) — skip rather than segfault
    if not single_line:
        from ..geometry.segmentize import MAX_OCR_PIXELS, det_projected_pixels
        if det_projected_pixels(crop.width, crop.height) > MAX_OCR_PIXELS:
            return None
    if crop.height < MIN_CROP_H:
        scale = max(2, MIN_CROP_H * 2 // max(1, crop.height))
        crop = crop.resize((crop.width * scale, crop.height * scale),
                           Image.LANCZOS)
    if not single_line:
        # det path: same gate as every other engine-facing image (the crop is
        # only compared as TEXT, so its scale needs no coordinate bookkeeping)
        from ..geometry.segmentize import _det_safe
        crop, _sx, _sy = _det_safe(crop)
    try:
        recognize = (getattr(backend, "recognize_line", backend.recognize)
                     if single_line else backend.recognize)
        segments = recognize(crop)
    except Exception:
        return None
    if not segments:
        return None
    return " ".join(s.text for s in sorted(segments, key=lambda s: (s.top, s.x0)))


def reread_agrees(spec: FieldSpec, evidence: str, reread_text: str) -> bool:
    """Type-aware agreement between the aligner's evidence and the re-read."""
    if spec.type in (FieldType.NUMBER, FieldType.PERCENT):
        doc = value_number_set(evidence)
        seen = set()
        # collect every number readable in the re-read text
        from ..align.matchers import _NUM_RUN, number_interpretations
        for m in _NUM_RUN.finditer(reread_text):
            seen |= number_interpretations(m.group(0))
        if {abs(d) for d in doc} & {abs(s) for s in seen}:
            return True
        return (_digit_multiset_subset(evidence, reread_text)
                or _clipped_tail(evidence, reread_text))
    ev = canon_value(evidence)
    got = canon_value(reread_text)
    if not ev or not got:
        return False
    if ev in got or got in ev:
        return True
    if spec.type == FieldType.DATE:
        return _digit_multiset_subset(evidence, reread_text)
    # the padded crop routinely grabs a stray neighboring glyph — for text
    # fields a near-match is agreement (ids stay strict; checksums cover them)
    if spec.type in (FieldType.TEXT, FieldType.BLOCK):
        from ..align.canon import ratio
        if ratio(ev, got) >= 0.85:
            return True
        if _char_multiset_close(ev, got):
            return True
        return _token_coverage(evidence, reread_text)
    return False


def _char_multiset_close(ev_canon: str, got_canon: str) -> bool:
    """OCR splits and fuses words between reads ("TSOVES" vs "TS OVES") —
    canonical glyph multisets barely differ when the pixels are the same."""
    from collections import Counter
    ev, got = Counter(ev_canon), Counter(got_canon)
    if sum(ev.values()) < 6:
        return False
    sym_diff = sum(((ev - got) + (got - ev)).values())
    return sym_diff <= 0.15 * (sum(ev.values()) + sum(got.values()))


def _token_coverage(evidence: str, reread_text: str) -> bool:
    """Dense-scan crops re-read multi-word values scrambled AND the padding
    grabs neighbor tokens by design — extra re-read tokens are expected noise.
    Agreement = the evidence's own substantial tokens are mostly present."""
    from collections import Counter
    from ..align.canon import canon_value

    def tokens(text: str) -> Counter:
        return Counter(t for w in text.split() if len(t := canon_value(w)) >= 2)

    ev, got = tokens(evidence), tokens(reread_text)
    if not ev or not got:
        return False
    covered = sum(len(t) * min(n, got[t]) for t, n in ev.items() if t in got)
    ev_len = sum(len(t) * n for t, n in ev.items())
    return covered >= 0.6 * ev_len


def _digit_multiset_subset(evidence: str, reread_text: str) -> bool:
    """Skewed crops re-read glyphs out of order ("88,53" → segments "53","88",
    separators lost) — the digit multiset still proves the right pixels sit
    under the box. Kept subset-wise: the padded crop may grab neighbor digits."""
    from collections import Counter
    ev = Counter(c for c in evidence if c.isdigit())
    if sum(ev.values()) < 3:  # too few glyphs to prove anything
        return False
    got = Counter(c for c in reread_text if c.isdigit())
    return not (ev - got)


def _clipped_tail(evidence: str, reread_text: str) -> bool:
    """Tight crops clip decimal tails ("68,70" re-reads as "68") — a re-read
    that is the HEAD or TAIL of the evidence's digit string confirms the
    pixels (either edge can clip).
    A multiset subset is not enough: "20,00" is built from a subset of
    "240,00"'s digits but is a different number — accepting it defeated
    the drifted-box defense. Two digits minimum, half covered."""
    ev = "".join(c for c in evidence if c.isdigit())
    got = "".join(c for c in reread_text if c.isdigit())
    if len(got) < 2 or not (ev.startswith(got) or ev.endswith(got)):
        return False
    return len(got) >= 0.5 * len(ev)
