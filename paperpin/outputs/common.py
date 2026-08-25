"""Shared output helpers: page image access for overlay/viewer rendering."""
from __future__ import annotations

from typing import Optional

from PIL import Image

from ..types import GroundResult


def get_page_images(result: GroundResult, pages: Optional[list[int]] = None
                    ) -> dict[int, Image.Image]:
    """Page rasters for rendering. The pipeline stores a lazy provider in
    result.meta["_page_image_provider"]; a plain dict under "_page_images"
    also works (tests, replay harnesses)."""
    static = result.meta.get("_page_images")
    provider = result.meta.get("_page_image_provider")
    wanted = pages if pages is not None else list(range(len(result.pages)))
    out: dict[int, Image.Image] = {}
    for idx in wanted:
        if static and idx in static:
            out[idx] = static[idx]
        elif provider is not None:
            out[idx] = provider(idx)
    if not out:
        raise ValueError(
            "no page images available — render from the pipeline's result object, "
            "or pass page images explicitly")
    return out


def fit_width(image: Image.Image, width: Optional[int] = None) -> Image.Image:
    """Scale a page raster to a target width, keeping aspect. Boxes are
    normalized, so any width renders them correctly — this only trades file
    size against sharpness."""
    if width is None or width == image.width:
        return image
    if width < 1:
        raise ValueError(f"width must be at least 1 pixel, got {width}")
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.LANCZOS)
