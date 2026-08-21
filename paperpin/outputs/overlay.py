"""Overlay rendering — boxes drawn on the ORIGINAL page raster (E-36).

Colors by status: green verified, amber low_confidence/ambiguous, red
not_found banner (no box to draw — it gets a margin note), grey not_present.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from ..types import GroundResult, Status

COLORS = {
    Status.VERIFIED: (52, 211, 153),        # green
    Status.LOW_CONFIDENCE: (251, 191, 36),  # amber
    Status.AMBIGUOUS: (251, 146, 60),       # orange
    Status.NOT_FOUND: (248, 113, 113),      # red
}


def _load_font(size: int):
    for name in ("consola.ttf", "arial.ttf", "DejaVuSansMono.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    font = ImageFont.load_default()
    if not hasattr(font, "size"):  # pre-FreeType bitmap fallback has no .size
        font.size = 11
    return font


def render_overlay(result: GroundResult, out_path: str,
                   page: Optional[int] = None,
                   page_images: Optional[dict[int, Image.Image]] = None) -> None:
    """Render one page (default: first page that has any boxed field)."""
    from .common import get_page_images
    if page is None:
        boxed_pages = [f.page for f in result if f.page is not None]
        page = boxed_pages[0] if boxed_pages else 0
    images = page_images if page_images is not None else get_page_images(result, [page])
    img = images[page].convert("RGB").copy()
    draw = ImageDraw.Draw(img, "RGBA")
    W, H = img.size
    font = _load_font(max(11, W // 110))

    missing: list[str] = []
    for f in result:
        if f.status == Status.NOT_FOUND:
            missing.append(f.name)
            continue
        if f.page != page or f.bbox is None:
            continue
        color = COLORS.get(f.status, (148, 163, 184))
        x0, y0, x1, y1 = (f.bbox[0] * W, f.bbox[1] * H, f.bbox[2] * W, f.bbox[3] * H)
        pad = max(2, H // 700)
        draw.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad],
                       outline=color + (255,), width=max(2, W // 800))
        draw.rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], fill=color + (34,))
        label = f.name
        tw = draw.textlength(label, font=font)
        ty = y0 - pad - font.size - 4
        if ty < 0:
            ty = y1 + pad + 2
        draw.rectangle([x0 - pad, ty - 1, x0 - pad + tw + 8, ty + font.size + 3],
                       fill=(11, 16, 32, 215))
        draw.text((x0 - pad + 4, ty), label, font=font, fill=color + (255,))

    if missing:
        band_h = font.size * (len(missing) + 2)
        draw.rectangle([0, 0, W, band_h], fill=(127, 29, 29, 210))
        draw.text((10, 4), "NOT FOUND on document (asserted by model):",
                  font=font, fill=(254, 226, 226, 255))
        for i, name in enumerate(missing, start=1):
            f = result[name]
            draw.text((10, 4 + i * (font.size + 2)),
                      f"✗ {name} = {f.value!r}", font=font, fill=(254, 202, 202, 255))

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
