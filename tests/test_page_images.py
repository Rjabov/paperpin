"""Page rasters: the pixels a viewer draws boxes on.

A normalized bbox is only useful next to an image it can be multiplied by, so
both ways of getting one — `result.page_image()` and `paperpin pages` — are
part of the contract another language depends on.
"""
import json
from pathlib import Path

import pytest

from paperpin.cli import main

DEMO = Path(__file__).parent.parent / "fixtures" / "demo"

pytestmark = pytest.mark.skipif(not (DEMO / "demo_invoice.pdf").exists(),
                                reason="demo doc not generated")


@pytest.fixture(scope="module")
def result():
    from paperpin import ground
    extraction = json.loads((DEMO / "demo_extraction.json").read_text("utf-8"))
    return ground(DEMO / "demo_invoice.pdf", extraction=extraction)


def test_bbox_times_page_image_size_lands_on_the_page(result):
    image = result.page_image(0)
    pinned = next(f for f in result if f.bbox)

    x0, y0, x1, y1 = pinned.bbox
    left, top = x0 * image.width, y0 * image.height
    right, bottom = x1 * image.width, y1 * image.height

    assert 0 <= left < right <= image.width
    assert 0 <= top < bottom <= image.height


def test_page_image_width_scales_proportionally(result):
    full = result.page_image(0)
    scaled = result.page_image(0, width=600)

    assert scaled.width == 600
    assert scaled.height == round(full.height * 600 / full.width)


def test_page_image_rejects_a_page_that_is_not_there(result):
    with pytest.raises(IndexError, match="no page 7"):
        result.page_image(7)


def test_pages_command_writes_one_file_per_page(tmp_path):
    assert main(["pages", str(DEMO / "demo_invoice.pdf"), "-o", str(tmp_path)]) == 0

    written = sorted(p.name for p in tmp_path.iterdir())
    assert written == ["page-0.png"]


def test_pages_command_honours_width_and_format(tmp_path):
    assert main(["pages", str(DEMO / "demo_invoice.pdf"), "-o", str(tmp_path),
                 "--width", "500", "--format", "jpg"]) == 0

    from PIL import Image
    with Image.open(tmp_path / "page-0.jpg") as image:
        assert image.width == 500
        assert image.format == "JPEG"


def test_pages_command_refuses_a_page_that_is_not_there(tmp_path, capsys):
    assert main(["pages", str(DEMO / "demo_invoice.pdf"), "-o", str(tmp_path),
                 "--page", "9"]) == 1

    assert "no page 9" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())
