"""The coordinate transform chain (HANDOVER §6.2).

Every preprocessing step that changes geometry (EXIF transpose, auto-orientation
rotation, resize, deskew) records an invertible transform from the space BEFORE
the step to the space AFTER it. Segments are produced in the final processed
space and mapped back through the inverse chain into upright-original space.

All transforms are affine and represented as 3x3 matrices acting on column
vectors (x, y, 1). This module is the ONLY place coordinate math lives —
convert once here, never inline (the prototype got bitten twice by inlining).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Transform:
    """One invertible affine step. `matrix` maps input-space → output-space.

    `out_size` is the (width, height) of the space the step produces —
    needed because 90/270 rotations swap dimensions.
    """

    name: str
    matrix: np.ndarray            # 3x3
    out_size: tuple[float, float]

    def inverse_matrix(self) -> np.ndarray:
        return np.linalg.inv(self.matrix)


def identity(size: tuple[float, float]) -> Transform:
    return Transform("identity", np.eye(3), size)


def scale(sx: float, sy: float, in_size: tuple[float, float]) -> Transform:
    m = np.array([[sx, 0, 0], [0, sy, 0], [0, 0, 1]], dtype=float)
    return Transform("scale", m, (in_size[0] * sx, in_size[1] * sy))


def rotate90(k: int, in_size: tuple[float, float]) -> Transform:
    """Rotate image content by k*90 degrees COUNTER-clockwise mathematically —
    matching PIL's Image.rotate(angle=90*k, expand=True) on a top-left-origin
    raster, where visually the content turns counter-clockwise.

    Derivations (w, h = input size; origin top-left, y grows down):
      k=1 (90° ccw):  (x, y) -> (y, w - x);       output size (h, w)
      k=2 (180°):     (x, y) -> (w - x, h - y);   output size (w, h)
      k=3 (270° ccw): (x, y) -> (h - y, x);       output size (h, w)
    """
    k = k % 4
    w, h = in_size
    if k == 0:
        return identity(in_size)
    if k == 1:
        m = np.array([[0, 1, 0], [-1, 0, w], [0, 0, 1]], dtype=float)
        return Transform("rot90ccw", m, (h, w))
    if k == 2:
        m = np.array([[-1, 0, w], [0, -1, h], [0, 0, 1]], dtype=float)
        return Transform("rot180", m, (w, h))
    m = np.array([[0, -1, h], [1, 0, 0], [0, 0, 1]], dtype=float)
    return Transform("rot270ccw", m, (h, w))


def exif_transpose_transform(orientation: int, in_size: tuple[float, float]) -> Transform:
    """Transform equivalent to PIL.ImageOps.exif_transpose for EXIF orientation
    tags 1..8. Input space = raw stored raster; output space = upright image.
    """
    w, h = in_size
    flip_h = np.array([[-1, 0, w], [0, 1, 0], [0, 0, 1]], dtype=float)
    if orientation in (1, 0):
        return identity(in_size)
    if orientation == 2:  # mirror horizontal
        return Transform("exif2", flip_h, (w, h))
    if orientation == 3:  # rotate 180
        return rotate90(2, in_size)
    if orientation == 4:  # mirror vertical = flip_h then rot180
        m = rotate90(2, in_size).matrix @ flip_h
        return Transform("exif4", m, (w, h))
    if orientation == 5:  # mirror horizontal + rotate 270 CW
        m = rotate90(1, (w, h)).matrix @ flip_h
        return Transform("exif5", m, (h, w))
    if orientation == 6:  # rotate 90 CW  == 270 ccw
        return Transform("exif6", rotate90(3, in_size).matrix, (h, w))
    if orientation == 7:  # mirror horizontal + rotate 90 CW
        m = rotate90(3, (w, h)).matrix @ flip_h
        return Transform("exif7", m, (h, w))
    if orientation == 8:  # rotate 270 CW == 90 ccw
        return Transform("exif8", rotate90(1, in_size).matrix, (h, w))
    return identity(in_size)


class TransformChain:
    """Ordered steps from upright-original space to final processed space.

    map_bbox_to_original() is what backends use: take a bbox measured in the
    processed raster and express it in upright-original coordinates.
    """

    def __init__(self, original_size: tuple[float, float]):
        self.original_size = original_size
        self.steps: list[Transform] = []

    @property
    def processed_size(self) -> tuple[float, float]:
        return self.steps[-1].out_size if self.steps else self.original_size

    def push(self, t: Transform) -> "TransformChain":
        self.steps.append(t)
        return self

    def _forward_matrix(self) -> np.ndarray:
        m = np.eye(3)
        for t in self.steps:
            m = t.matrix @ m
        return m

    def _inverse_matrix(self) -> np.ndarray:
        return np.linalg.inv(self._forward_matrix())

    def map_points_to_original(self, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        inv = self._inverse_matrix()
        arr = np.array([[x, y, 1.0] for x, y in pts], dtype=float).T
        out = inv @ arr
        return [(float(x), float(y)) for x, y in zip(out[0], out[1])]

    def map_bbox_to_original(self, bbox: tuple[float, float, float, float]
                             ) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = bbox
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        mapped = self.map_points_to_original(corners)
        xs = [p[0] for p in mapped]
        ys = [p[1] for p in mapped]
        return (min(xs), min(ys), max(xs), max(ys))

    def map_bbox_to_processed(self, bbox: tuple[float, float, float, float]
                              ) -> tuple[float, float, float, float]:
        fwd = self._forward_matrix()
        x0, y0, x1, y1 = bbox
        corners = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]], dtype=float).T
        out = fwd @ corners
        xs, ys = out[0], out[1]
        return (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))


def normalize_bbox(bbox: tuple[float, float, float, float],
                   size: tuple[float, float]) -> tuple[float, float, float, float]:
    w, h = size
    x0, y0, x1, y1 = bbox
    nx0, nx1 = sorted((x0 / w, x1 / w))
    ny0, ny1 = sorted((y0 / h, y1 / h))

    def clamp(v: float) -> float:
        return min(1.0, max(0.0, v))

    return (clamp(nx0), clamp(ny0), clamp(nx1), clamp(ny1))


def denormalize_bbox(bbox: tuple[float, float, float, float],
                     size: tuple[float, float]) -> tuple[float, float, float, float]:
    w, h = size
    x0, y0, x1, y1 = bbox
    return (x0 * w, y0 * h, x1 * w, y1 * h)


def pdf_top_to_bottom_origin(bbox: tuple[float, float, float, float],
                             page_height: float) -> tuple[float, float, float, float]:
    """Convert a top-origin (pdfplumber-style) box to bottom-origin PDF points
    (what PDF annotation writers expect). y0/y1 swap roles.
    """
    x0, top, x1, bottom = bbox
    return (x0, page_height - bottom, x1, page_height - top)
