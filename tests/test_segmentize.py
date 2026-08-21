"""Geometry-stage safety: no code path may hand the OCR engine an image
bigger than it survives (~25MP segfaults the det model), and downscaled OCR
input must never leak into published coordinates.
"""
from PIL import Image

from paperpin.geometry.segmentize import MAX_OCR_PIXELS, segmentize
from paperpin.intake.loader import MAX_RASTER_LONG_SIDE, Page
from paperpin.types import Segment


class RecordingBackend:
    """Fake OCR: records every image it gets, returns one low-conf segment
    so the rescue pass always triggers."""
    name = "fake"

    def __init__(self):
        self.sizes = []

    def recognize(self, image):
        self.sizes.append(image.size)
        w, h = image.size
        # tiny text -> median height below the rescue threshold
        return [Segment(text="x", x0=0, top=0, x1=8, bottom=8, conf=0.4)]


def test_rescue_upscale_never_exceeds_pixel_budget():
    img = Image.new("RGB", (4200, 3000))  # a raster already at the cap
    page = Page(index=0, size=(4200.0, 3000.0), route="ocr", image=img)
    backend = RecordingBackend()
    segmentize(page, backend, doc_sha="0" * 64, use_cache=False)
    assert all(w * h <= MAX_OCR_PIXELS for (w, h) in backend.sizes)


def test_oversized_image_is_ocrd_downscaled_but_published_in_original_space():
    img = Image.new("RGB", (8400, 6000))  # 50MP camera image
    page = Page(index=0, size=(8400.0, 6000.0), route="ocr", image=img)

    class OneBox:
        name = "fake"

        def recognize(self, image):
            self.size = image.size
            w, h = image.size
            # a segment covering the full OCR'd image
            return [Segment(text="hello", x0=0, top=0, x1=w, bottom=h, conf=0.9)]

    backend = OneBox()
    ps = segmentize(page, backend, doc_sha="1" * 64, use_cache=False)
    assert max(backend.size) <= MAX_RASTER_LONG_SIDE
    seg = ps.segments[0]
    # published coordinates are original-image space
    assert abs(seg.x1 - 8400.0) < 2.0
    assert abs(seg.bottom - 6000.0) < 2.0


def test_rescue_reocr_respects_pixel_budget():
    # verified by review: the not_found rescue crop (0.5 page widths x 8.5 row
    # heights, then x3) reached 57-106MP on big rasters — past the det ceiling
    from paperpin.align.rows import build_rows
    from paperpin.types import FieldResult, FieldSpec, Status
    from paperpin.verify.rescue import rescue_not_founds

    page_img = Image.new("RGB", (2969, 4200))

    class Recorder:
        name = "fake"
        sizes = []

        def recognize(self, image):
            self.sizes.append(image.size)
            return []

    # anchor row: a tall heading near the top so the neighborhood is huge
    seg_row = build_rows([Segment(text="faktura celkem suma", x0=10, top=10,
                                  x1=2000, bottom=210, conf=0.9)])
    fr = FieldResult(name="total", value="1 234,56", status=Status.NOT_FOUND,
                     confidence=0.0, quote="celkem suma 1 234,56")
    backend = Recorder()
    rescue_not_founds({"total": fr}, {"total": FieldSpec(name="total")},
                      seg_row, {0: (2969.0, 4200.0)}, {0: "ocr"},
                      lambda i: page_img, backend)
    from paperpin.geometry.segmentize import MAX_OCR_PIXELS
    assert backend.sizes, "rescue never ran — test setup broken"
    assert all(w * h <= MAX_OCR_PIXELS for (w, h) in backend.sizes)


def test_main_ocr_path_budgets_raw_and_projected_pixels():
    # dishboard 2026-08-20: six real docs segfaulted the det model — a
    # near-square raster passes the long-side cap at >=14MP raw and reaches
    # the engine unbudgeted (the caps existed but the primary recognize()
    # call never applied them)
    from paperpin.geometry.segmentize import det_projected_pixels
    img = Image.new("RGB", (4200, 4200))  # 17.6MP, long side exactly at cap
    page = Page(index=0, size=(4200.0, 4200.0), route="ocr", image=img)
    backend = RecordingBackend()
    segmentize(page, backend, doc_sha="2" * 64, use_cache=False)
    assert backend.sizes
    assert all(w * h <= MAX_OCR_PIXELS for (w, h) in backend.sizes)
    assert all(det_projected_pixels(w, h) <= MAX_OCR_PIXELS
               for (w, h) in backend.sizes)


def test_main_ocr_downscale_publishes_original_space_coords():
    class OneBox:
        name = "fake"

        def recognize(self, image):
            w, h = image.size
            return [Segment(text="hello", x0=0, top=0, x1=w, bottom=h, conf=0.9)]

    img = Image.new("RGB", (4200, 4200))
    page = Page(index=0, size=(4200.0, 4200.0), route="ocr", image=img)
    ps = segmentize(page, OneBox(), doc_sha="3" * 64, use_cache=False)
    seg = ps.segments[0]
    assert abs(seg.x1 - 4200.0) < 3.0
    assert abs(seg.bottom - 4200.0) < 3.0


def test_thin_strip_escapes_the_det_upscale_zone():
    # uniform downscale cannot shrink a strip's det projection (the engine
    # re-upscales the short side, projection ~ 736^2 x aspect) — the strip
    # must be padded up to the det floor instead, keeping content coords
    from paperpin.geometry.segmentize import det_projected_pixels
    img = Image.new("RGB", (4200, 60))  # projection ~38MP, the segfault zone
    page = Page(index=0, size=(4200.0, 60.0), route="ocr", image=img)
    backend = RecordingBackend()
    segmentize(page, backend, doc_sha="4" * 64, use_cache=False)
    assert backend.sizes
    assert all(det_projected_pixels(w, h) <= MAX_OCR_PIXELS
               for (w, h) in backend.sizes)
    assert all(w * h <= MAX_OCR_PIXELS for (w, h) in backend.sizes)


def test_det_projection_accounts_for_internal_upscale():
    from paperpin.geometry.segmentize import DET_MIN_SIDE, det_projected_pixels
    assert det_projected_pixels(4000, 3000) == 12_000_000  # short side >= 736
    thin = det_projected_pixels(4400, 90)  # det scales 90 -> 736 internally
    assert thin > 25_000_000  # the strip that segfaulted in the wild


def test_rescue_strip_never_projects_past_det_budget():
    from paperpin.align.rows import build_rows
    from paperpin.geometry.segmentize import MAX_OCR_PIXELS, det_projected_pixels
    from paperpin.types import FieldResult, FieldSpec, Status
    from paperpin.verify.rescue import rescue_not_founds

    page_img = Image.new("RGB", (4200, 3000))

    class Recorder:
        name = "fake"
        sizes = []

        def recognize(self, image):
            self.sizes.append(image.size)
            return []

    # a short row -> the re-OCR neighborhood is a long thin strip
    rows = build_rows([Segment(text="celkem suma faktura", x0=100, top=1500,
                               x1=900, bottom=1530, conf=0.9)])
    fr = FieldResult(name="total", value="9 876,54", status=Status.NOT_FOUND,
                     confidence=0.0, quote="celkem suma 9 876,54")
    backend = Recorder()
    rescue_not_founds({"total": fr}, {"total": FieldSpec(name="total")},
                      rows, {0: (4200.0, 3000.0)}, {0: "ocr"},
                      lambda i: page_img, backend)
    assert backend.sizes
    assert all(det_projected_pixels(w, h) <= MAX_OCR_PIXELS
               for (w, h) in backend.sizes)


def test_backend_char_boxes_are_mapped_to_original_on_rotated_pages(monkeypatch):
    # §6.2 lock ("bit the prototype twice"): backend-supplied char boxes are
    # built in the processed (rotated) frame and MUST come back through the
    # chain — left in processed space they silently poison every sub-box
    from paperpin.geometry import segmentize as sg
    from paperpin.geometry.transform import TransformChain, rotate90

    monkeypatch.setattr(sg, "_best_orientation", lambda img, backend: 1)

    class TwoChars:
        name = "fake"

        def recognize(self, image):
            w, h = image.size
            return [Segment(text="ab", x0=0, top=0, x1=64, bottom=32, conf=0.9,
                            char_boxes=[(0, 0, 32, 32), (32, 0, 64, 32)])]

    img = Image.new("RGB", (608, 800))
    page = Page(index=0, size=(608.0, 800.0), route="ocr", image=img)
    ps = sg.segmentize(page, TwoChars(), doc_sha="5" * 64, use_cache=False)
    seg_out = ps.segments[0]

    chain = TransformChain((608.0, 800.0))
    chain.push(rotate90(1, (608.0, 800.0)))
    expected = [chain.map_bbox_to_original((0, 0, 32, 32)),
                chain.map_bbox_to_original((32, 0, 64, 32))]
    assert seg_out.char_boxes is not None
    for got, want in zip(seg_out.char_boxes, expected):
        assert all(abs(g - w) < 0.5 for g, w in zip(got, want)), \
            (seg_out.char_boxes, expected)
