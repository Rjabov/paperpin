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
