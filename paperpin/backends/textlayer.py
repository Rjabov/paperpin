"""Text-layer segment extraction — exact, free, always preferred when sane (§4.3).

Coordinates are PDF points with TOP-left origin (pdfplumber convention), which
is already our upright-original space for PDF pages — no transform chain needed.
Per-character boxes ride along for exact sub-box slicing (§6.4a).
"""
from __future__ import annotations

from ..types import Segment


def extract_segments(pdf_page) -> list[Segment]:
    words = pdf_page.extract_words(keep_blank_chars=False, use_text_flow=False)
    chars = pdf_page.chars
    # scanning every char for every word is quadratic (measured ~0.7s on a
    # dense page); bucketing by vertical band makes the lookup local
    buckets: dict[int, list[dict]] = {}
    for c in chars:
        buckets.setdefault(int(c["top"] // 8), []).append(c)
    segments: list[Segment] = []
    for w in words:
        seg = Segment(
            text=w["text"],
            x0=float(w["x0"]), top=float(w["top"]),
            x1=float(w["x1"]), bottom=float(w["bottom"]),
            conf=1.0,
        )
        lo, hi = int((w["top"] - 1) // 8), int((w["bottom"] + 1) // 8)
        near = [c for b in range(lo, hi + 1) for c in buckets.get(b, ())]
        seg.char_boxes = _match_chars(w, near)
        segments.append(seg)
    return segments


def _match_chars(word: dict, chars: list[dict]):
    """Attach per-char boxes to a word by bbox containment; None if the count
    doesn't line up (ligatures, odd encodings) — caller falls back to
    proportional slicing."""
    x0, x1 = word["x0"], word["x1"]
    top, bottom = word["top"], word["bottom"]
    tol = 1.0
    inside = [c for c in chars
              if c["x0"] >= x0 - tol and c["x1"] <= x1 + tol
              and c["top"] >= top - tol and c["bottom"] <= bottom + tol]
    inside.sort(key=lambda c: c["x0"])
    text = "".join(c["text"] for c in inside)
    if text != word["text"]:
        return None
    return [(float(c["x0"]), float(c["top"]), float(c["x1"]), float(c["bottom"]))
            for c in inside]
