"""Degradation matrix (HANDOVER §9.1b, extending torture2's grid):
clean render → rotated / blur+jpeg / photo-sim (shadow band + noise + slight
skew) variants of a PDF page, with ground-truth boxes mapped through the same
TransformChain the pipeline uses (dogfooding §6.2).
"""
from __future__ import annotations

import io
import json
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from paperpin.geometry.transform import TransformChain, rotate90

RENDER_DPI = 200


def render_pdf_page(pdf_path: Path, dpi: int = RENDER_DPI) -> Image.Image:
    import pdfplumber
    with pdfplumber.open(str(pdf_path)) as pdf:
        return pdf.pages[0].to_image(resolution=dpi).original.convert("RGB")


def variant_clean(img: Image.Image) -> tuple[Image.Image, TransformChain]:
    return img, TransformChain((img.width, img.height))


def variant_rot90(img: Image.Image, k: int = 3) -> tuple[Image.Image, TransformChain]:
    chain = TransformChain((img.width, img.height))
    chain.push(rotate90(k, (img.width, img.height)))
    return img.rotate(90 * k, expand=True), chain


def variant_blur_jpeg(img: Image.Image, blur: float = 1.1, q: int = 30
                      ) -> tuple[Image.Image, TransformChain]:
    out = img.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=q)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB"), \
        TransformChain((img.width, img.height))


def variant_photo_sim(img: Image.Image, seed: int = 7
                      ) -> tuple[Image.Image, TransformChain]:
    """Phone-photo simulation: slight rotation, shadow gradient, sensor noise,
    mild JPEG. Geometry: pure small rotation (recorded in the chain)."""
    rng = random.Random(seed)
    angle = rng.uniform(-2.2, 2.2)
    w, h = img.width, img.height
    rad = math.radians(angle)
    # rotate about center without expand: same size, chain = R_center
    rot = img.rotate(angle, resample=Image.BICUBIC, fillcolor=(210, 205, 198))
    arr = np.asarray(rot).astype(np.float32)
    yy = np.linspace(0, 1, h)[:, None, None]
    xx = np.linspace(0, 1, w)[None, :, None]
    shade = 1.0 - 0.28 * np.clip((xx - 0.15) * 1.4, 0, 1) * np.clip((yy - 0.1) * 1.2, 0, 1)
    arr *= shade
    arr += np.random.default_rng(seed).normal(0, 6.0, arr.shape)
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=62)
    out = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")

    cos, sin = math.cos(rad), math.sin(rad)
    cx, cy = w / 2, h / 2
    # PIL rotate(angle) turns content ccw about the center; in top-left-origin
    # raster coords the mapping of a source point to its new location is:
    m = np.array([[cos, sin, cx - cos * cx - sin * cy],
                  [-sin, cos, cy + sin * cx - cos * cy],
                  [0, 0, 1]], dtype=float)
    from paperpin.geometry.transform import Transform
    chain = TransformChain((w, h))
    chain.push(Transform("photo_rot", m, (w, h)))
    return out, chain


VARIANTS = {
    "clean": lambda img: variant_clean(img),
    "rot270": lambda img: variant_rot90(img, 3),
    "blur_jpeg30": lambda img: variant_blur_jpeg(img),
    "photo_sim": lambda img: variant_photo_sim(img),
}


def degrade_corpus(corpus_dir: Path, out_dir: Path,
                   variants: list[str] = ("clean", "rot270", "blur_jpeg30", "photo_sim")
                   ) -> list[dict]:
    """For every corpus PDF, emit JPG variants + truth JSON with boxes mapped
    into each variant's own pixel space."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((corpus_dir / "manifest.json").read_text())
    entries = []
    for name in manifest:
        meta = json.loads((corpus_dir / f"{name}.json").read_text(encoding="utf-8"))
        img = render_pdf_page(corpus_dir / f"{name}.pdf")
        for variant in variants:
            vimg, chain = VARIANTS[variant](img)
            vname = f"{name}_{variant}"
            vimg.save(out_dir / f"{vname}.jpg", quality=88)
            truth = {}
            for fname, f in meta["truth"].items():
                x0, y0, x1, y1 = f["bbox"]
                px = (x0 * img.width, y0 * img.height, x1 * img.width, y1 * img.height)
                vx = chain.map_bbox_to_processed(px)
                truth[fname] = {**f, "bbox": [vx[0] / vimg.width, vx[1] / vimg.height,
                                              vx[2] / vimg.width, vx[3] / vimg.height]}
            payload = {**meta, "variant": variant, "truth": truth}
            (out_dir / f"{vname}.json").write_text(
                json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
            entries.append({"name": vname, "variant": variant, "source": name})
    (out_dir / "manifest.json").write_text(json.dumps(entries, indent=1), encoding="utf-8")
    return entries
