"""Tesseract backend — fallback only, clearly labeled (HANDOVER §4.3).

Documented honestly: in the prototype torture tests tesseract lost badly on
rotated and JPEG-crushed inputs (5/21 fields on compressed vs RapidOCR's 16+)
and fails on inverse-contrast blocks (E-13). It exists for environments where
ONNX runtime is unavailable.
"""
from __future__ import annotations

import os

from PIL import Image

from ..types import Segment


class TesseractBackend:
    name = "tesseract"

    def recognize(self, image: Image.Image) -> list[Segment]:
        try:
            import pytesseract
            from pytesseract import Output
        except ImportError as e:
            raise ImportError(
                "tesseract backend needs pytesseract + a system tesseract binary"
            ) from e
        lang = os.environ.get("PAPERPIN_TESSERACT_LANG") or None
        data = pytesseract.image_to_data(image, output_type=Output.DICT,
                                         lang=lang)
        segments: list[Segment] = []
        n = len(data["text"])
        for i in range(n):
            text = (data["text"][i] or "").strip()
            conf = float(data["conf"][i])
            if not text or conf < 0:
                continue
            x, y = float(data["left"][i]), float(data["top"][i])
            w, h = float(data["width"][i]), float(data["height"][i])
            segments.append(Segment(
                text=text, x0=x, top=y, x1=x + w, bottom=y + h,
                conf=conf / 100.0,
            ))
        return segments
