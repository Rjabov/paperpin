"""The overlay PNG — `result.overlay()`, `paperpin overlay`, `--overlay`.

It is the proof artefact people paste into a ticket, and until the coverage
gate went in nothing executed it at all. These assert what the image has to
show: a box in the status colour around the located value, and the
hallucination flag rendered as a banner rather than a box on the page.
"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from paperpin.outputs.overlay import COLORS
from paperpin.types import Status

DEMO = Path(__file__).parent.parent / "fixtures" / "demo"

pytestmark = pytest.mark.skipif(not (DEMO / "demo_invoice.pdf").exists(),
                                reason="demo doc not generated")


@pytest.fixture(scope="module")
def result():
    from paperpin import ground
    extraction = json.loads((DEMO / "demo_extraction.json").read_text("utf-8"))
    return ground(DEMO / "demo_invoice.pdf", extraction=extraction)


def _near(pixels: np.ndarray, colour: tuple, tolerance: int = 30) -> np.ndarray:
    """Mask of pixels within `tolerance` of an exact colour, per channel. The
    outline is drawn opaque, so it survives anti-aliasing at this distance."""
    return (np.abs(pixels.astype(int) - np.array(colour)) <= tolerance).all(axis=-1)


def test_overlay_writes_a_png_the_size_of_the_page(result, tmp_path):
    out = tmp_path / "proof.png"
    result.overlay(out)

    with Image.open(out) as rendered:
        assert rendered.format == "PNG"
        assert rendered.size == result.page_image(0).size


def test_verified_fields_are_boxed_in_the_verified_colour(result, tmp_path):
    out = tmp_path / "proof.png"
    result.overlay(out)

    with Image.open(out) as rendered:
        pixels = np.asarray(rendered.convert("RGB"))
    assert _near(pixels, COLORS[Status.VERIFIED]).any(), \
        "no green outline anywhere — verified fields were not drawn"


def test_the_box_is_drawn_around_the_field_it_belongs_to(result, tmp_path):
    """A box in the right colour somewhere on the page is not enough: it has
    to be around this field's bbox."""
    out = tmp_path / "proof.png"
    result.overlay(out)
    field = result["total_due"]

    with Image.open(out) as rendered:
        pixels = np.asarray(rendered.convert("RGB"))
    height, width = pixels.shape[:2]
    x0, y0, x1, y1 = field.bbox
    margin = 12
    window = pixels[max(0, int(y0 * height) - margin):int(y1 * height) + margin,
                    max(0, int(x0 * width) - margin):int(x1 * width) + margin]

    assert _near(window, COLORS[Status.VERIFIED]).any(), \
        f"no verified-coloured outline around total_due at {field.bbox}"


def test_a_hallucination_becomes_a_banner_not_a_box(result, tmp_path):
    """`not_found` has no location, so it must never be drawn on the page —
    it goes in the red band across the top instead."""
    out = tmp_path / "proof.png"
    result.overlay(out)
    assert result["approved_by"].status == Status.NOT_FOUND

    with Image.open(out) as rendered:
        pixels = np.asarray(rendered.convert("RGB"))
    banner = _near(pixels[:60], (127, 29, 29), tolerance=60)
    below = _near(pixels[200:], COLORS[Status.NOT_FOUND])

    assert banner.any(), "no NOT FOUND banner at the top of the overlay"
    assert not below.any(), "a not_found field was drawn as a box on the page"


def test_overlay_renders_a_page_that_has_no_boxes(result, tmp_path):
    """Asking for a bare page must produce the page, not an exception."""
    out = tmp_path / "blank.png"
    blank = Image.new("RGB", (400, 560), "white")

    from paperpin.outputs.overlay import render_overlay
    render_overlay(result, out, page=0, page_images={0: blank})

    with Image.open(out) as rendered:
        assert rendered.size == (400, 560)


def test_overlay_cli_renders_from_a_saved_result(result, tmp_path, capsys):
    """`paperpin overlay FILE result.json` rebuilds the result from JSON, so
    it exercises from_dict as well as the renderer."""
    from paperpin.cli import main

    saved = tmp_path / "result.json"
    result.save(saved)
    out = tmp_path / "cli.png"

    assert main(["overlay", str(DEMO / "demo_invoice.pdf"), str(saved),
                 "-o", str(out)]) == 0

    assert f"overlay → {out}" in capsys.readouterr().out
    with Image.open(out) as rendered:
        pixels = np.asarray(rendered.convert("RGB"))
    assert _near(pixels, COLORS[Status.VERIFIED]).any()
