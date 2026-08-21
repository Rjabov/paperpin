"""Intake raster behavior: oversized scan-PDF pages must not explode.

Scan PDFs sometimes store the page at pixel dimensions expressed as points
(2500+ pt = 90cm "paper"). A fixed render dpi then produces 30-90MP rasters,
which segfault the OCR det model. The raster cap trades dpi for a bounded
pixel budget on such pages; normal pages keep the requested dpi.
"""
from PIL import Image

from paperpin.intake.loader import MAX_RASTER_LONG_SIDE, Page


class _StubPdfPage:
    """Captures the resolution raster() asks for and returns a tiny image."""

    def __init__(self):
        self.requested = None

    def to_image(self, resolution):
        self.requested = resolution

        class _Img:
            original = Image.new("RGB", (10, 10))

        return _Img()


def _page(size) -> tuple[Page, _StubPdfPage]:
    stub = _StubPdfPage()
    return Page(index=0, size=size, route="ocr", pdf_page=stub), stub


def test_should_keep_requested_dpi_on_normal_pages():
    page, stub = _page((595.0, 842.0))  # A4
    page.raster(dpi=220)
    assert stub.requested == 220


def test_should_cap_raster_of_oversized_scan_pages():
    page, stub = _page((2568.0, 3887.0))  # real-corpus segfault doc
    page.raster(dpi=220)
    long_px = 3887.0 * stub.requested / 72.0
    assert long_px <= MAX_RASTER_LONG_SIDE + 1


def test_should_cap_default_dpi_render_too():
    page, stub = _page((2608.0, 3859.0))
    page.raster()  # default 150 dpi path
    long_px = 3859.0 * stub.requested / 72.0
    assert long_px <= MAX_RASTER_LONG_SIDE + 1


def test_junk_prefixed_pdf_still_loads_as_pdf():
    import io

    from paperpin.intake.loader import load_document
    real = open("fixtures/corpus/inv_en_left.pdf", "rb").read()
    doc = load_document(b"\xef\xbb\xbfGARBAGE" + real, filename="junk.pdf")
    assert doc.kind == "pdf"
    assert len(doc.pages) >= 1


def test_multi_frame_tiff_yields_all_pages():
    import io

    from PIL import Image

    from paperpin.intake.loader import load_document
    frames = [Image.new("RGB", (100, 80), c) for c in ("white", "black", "gray")]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    doc = load_document(buf.getvalue(), filename="multi.tiff")
    assert len(doc.pages) == 3
    assert [p.index for p in doc.pages] == [0, 1, 2]


def test_byo_extraction_path_errors_are_actionable():
    from pathlib import Path

    import pytest

    from paperpin.adapters.base import load_byo_extraction
    with pytest.raises(FileNotFoundError):
        load_byo_extraction(Path("/nonexistent/extraction.json"))
    with pytest.raises(ValueError):
        load_byo_extraction("not json and not a file")
    assert load_byo_extraction('{"total": "5"}') == {"total": "5"}


def test_viewer_escapes_untrusted_values(tmp_path):
    from PIL import Image

    from paperpin.outputs.viewer import render_viewer
    from paperpin.types import FieldResult, GroundResult, PageInfo, Status
    evil = '<img src=x onerror=alert(1)></script><script>alert(2)</script>'
    fields = {"total": FieldResult(name="total", value=evil,
                                   status=Status.NOT_FOUND, confidence=0.0)}
    result = GroundResult(
        fields=fields,
        pages=[PageInfo(index=0, width=100, height=100, route="ocr")],
        source='<b>evil</b>.pdf',
        meta={"_page_images": {0: Image.new("RGB", (100, 100))}})
    out = tmp_path / "v.html"
    render_viewer(result, str(out))
    html = out.read_text(encoding="utf-8")
    # the value may sit inside the JSON data block (a JS string literal),
    # but it must not be able to BREAK OUT of it or render as markup:
    assert html.count("</script>") == 1              # only the template's own
    assert "\\u003c" in html                         # every '<' in data escaped
    assert "<b>evil</b>" not in html                 # title escaped
    assert ".innerHTML" not in html                  # fields built via textContent


def test_multi_frame_tiff_is_bounded():
    # verified by review: a 453KB G4 TIFF of 500 blank pages expanded to
    # ~7GB RSS. Frame count and total decoded pixels are capped now.
    import io

    from PIL import Image

    from paperpin.intake.loader import MAX_IMAGE_PAGES, load_document
    frames = [Image.new("1", (2000, 2600), 1) for _ in range(120)]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:],
                   compression="group4")
    doc = load_document(buf.getvalue(), filename="many.tiff")
    assert len(doc.pages) <= MAX_IMAGE_PAGES


def test_exif_orientation_is_applied_before_ocr():
    # E-2 lock: phones store sideways pixels + an EXIF flag; ignoring the
    # flag hands the OCR stage a rotated raster with upright-looking coords
    import io

    from paperpin.intake.loader import load_document
    from PIL import Image

    im = Image.new("RGB", (100, 50), "white")
    ex = Image.Exif()
    ex[274] = 6  # rotate 90 CW to display upright
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=ex)
    doc = load_document(buf.getvalue(), filename="sideways.jpg")
    assert doc.pages[0].raster().size == (50, 100)


def test_dropped_tiff_frames_are_counted_not_silent():
    # B-P1-2 (round-3): a 60-frame fax loaded as 50 pages with no record
    # anywhere — a value on page 55 then wore the hallucination flag for a
    # page the pipeline never looked at
    import io

    from paperpin.intake.loader import MAX_IMAGE_PAGES, load_document
    from PIL import Image

    frames = [Image.new("1", (60, 40), 1) for _ in range(MAX_IMAGE_PAGES + 10)]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:],
                   compression="group4")
    doc = load_document(buf.getvalue(), filename="sixty.tiff")
    assert len(doc.pages) == MAX_IMAGE_PAGES
    assert doc.pages_dropped == 10


ZERO_PAGE_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 0 0]/Resources<<>>>>endobj
xref
0 4
0000000000 65535 f
trailer<</Size 4/Root 1 0 R>>
startxref
0
%%EOF"""


def test_zero_size_pdf_page_never_divides_by_zero():
    # B-P2-2 (round-3): MediaBox [0 0 0 0] raised a raw ZeroDivisionError
    # and killed the whole document (a middle page killed its neighbours)
    import paperpin

    res = paperpin.ground(ZERO_PAGE_PDF, extraction={"total": "1"})
    assert res["total"].status.value in ("not_found", "not_present")


def test_undecodable_dotenv_never_kills_the_cli(tmp_path, monkeypatch):
    # B-P2-3 (round-3): UnicodeDecodeError is not an OSError — a UTF-16
    # .env (Windows `>` redirect) crashed every command including version
    (tmp_path / ".env").write_bytes(b"GEMINI_API_KEY=abc\nP=Pa\xffss\xfe\n")
    monkeypatch.chdir(tmp_path)
    from paperpin.env import load_dotenv
    load_dotenv()  # must simply skip the unreadable file


def test_save_never_truncates_the_previous_result(tmp_path):
    # B-P2-4 (round-3): open(path, "w") truncated before json.dump streamed;
    # a lone surrogate (legal for json.loads!) then died mid-write and
    # destroyed the previous good file. NaN must not reach the file either.
    import json

    import paperpin

    res = paperpin.ground("fixtures/corpus/inv_en_left.pdf",
                          extraction=json.loads('{"a": "\\ud800ok", "b": NaN}'))
    out = tmp_path / "r.json"
    out.write_text("PREVIOUS GOOD")
    res.save(str(out))
    data = json.loads(out.read_text())  # valid JSON, file fully replaced
    assert "NaN" not in out.read_text()
    assert data["fields"]["a"]["value"].endswith("ok")
