"""§6.2: the transform chain must round-trip exactly — it bit the prototype twice."""
import math

import numpy as np
import pytest

from paperpin.geometry.transform import (TransformChain, exif_transpose_transform,
                                         normalize_bbox, pdf_top_to_bottom_origin,
                                         rotate90, scale)


@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_rotate90_roundtrip(k):
    chain = TransformChain((200, 100))
    chain.push(rotate90(k, (200, 100)))
    bbox = (10, 20, 50, 40)
    processed = chain.map_bbox_to_processed(bbox)
    back = chain.map_bbox_to_original(processed)
    assert all(abs(a - b) < 1e-9 for a, b in zip(back, bbox))


def test_rotate90_swaps_dims():
    t = rotate90(1, (200, 100))
    assert t.out_size == (100, 200)


def test_rot90_ccw_corner_mapping():
    # top-right corner of a 200x100 image lands at the top-left after 90° ccw
    chain = TransformChain((200, 100))
    chain.push(rotate90(1, (200, 100)))
    (x, y), = [chain.map_bbox_to_processed((200, 0, 200, 0))[:2]]  # point-ish
    assert (round(x), round(y)) == (0, 0)


@pytest.mark.parametrize("orientation", range(1, 9))
def test_exif_transforms_invertible(orientation):
    t = exif_transpose_transform(orientation, (300, 200))
    assert abs(np.linalg.det(t.matrix)) == pytest.approx(1.0)
    inv = t.inverse_matrix()
    assert np.allclose(inv @ t.matrix, np.eye(3))


def test_chain_rotation_plus_scale():
    chain = TransformChain((200, 100))
    chain.push(rotate90(3, (200, 100)))
    chain.push(scale(2.0, 2.0, chain.steps[-1].out_size))
    assert chain.processed_size == (200, 400)
    bbox = (10, 20, 50, 40)
    back = chain.map_bbox_to_original(chain.map_bbox_to_processed(bbox))
    assert all(abs(a - b) < 1e-9 for a, b in zip(back, bbox))


def test_normalize_clamps_and_sorts():
    assert normalize_bbox((-5, 10, 120, 40), (100, 50)) == (0.0, 0.2, 1.0, 0.8)


def test_pdf_y_flip():
    # a box near the TOP of a 842pt page has small top-origin y, large bottom-origin y
    top_origin = (100, 30, 200, 50)
    x0, y0, x1, y1 = pdf_top_to_bottom_origin(top_origin, 842)
    assert (x0, x1) == (100, 200)
    assert y0 == 842 - 50 and y1 == 842 - 30
