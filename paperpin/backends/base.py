"""OCR backend contract (HANDOVER §4.3).

A backend takes an upright RGB PIL image and returns Segments in THAT image's
pixel space (top-left origin). Orientation search, preprocessing and mapping
back through the transform chain are the pipeline's job, not the backend's.
"""
from __future__ import annotations

from typing import Protocol

from PIL import Image

from ..types import Segment


class OcrBackend(Protocol):
    """Required: recognize(). Two OPTIONAL hooks the pipeline probes with
    getattr and uses when present:

    - recognize_line(image) -> list[Segment]: cheap single-line read for
      crop re-checks (no detection pass).
    - flipped_majority(image) -> Optional[bool]: whether most detected
      lines read 180°-flipped — the 0-vs-180 orientation discriminator.
    """

    name: str

    def recognize(self, image: Image.Image) -> list[Segment]:
        ...


def get_backend(name: str) -> OcrBackend:
    if name in ("auto", "rapidocr"):
        from .rapidocr_backend import RapidOcrBackend
        return RapidOcrBackend()
    if name == "tesseract":
        from .tesseract_backend import TesseractBackend
        return TesseractBackend()
    raise ValueError(f"unknown OCR backend: {name!r} (available: rapidocr, tesseract)")
