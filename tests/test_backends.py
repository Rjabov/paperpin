"""OCR backend knobs that must work without loading any model."""
import onnxruntime as ort

from paperpin.backends.rapidocr_backend import _capped_session_options, _ocr_thread_cap


def test_should_read_thread_cap_from_env(monkeypatch):
    monkeypatch.delenv("PAPERPIN_OCR_THREADS", raising=False)
    assert _ocr_thread_cap() is None          # unset -> no cap, full speed
    monkeypatch.setenv("PAPERPIN_OCR_THREADS", "0")
    assert _ocr_thread_cap() is None          # 0 -> explicit "no cap"
    monkeypatch.setenv("PAPERPIN_OCR_THREADS", "2")
    assert _ocr_thread_cap() == 2
    monkeypatch.setenv("PAPERPIN_OCR_THREADS", "junk")
    assert _ocr_thread_cap() is None          # garbage never breaks OCR


def test_capped_options_pin_intra_op_threads():
    cls = _capped_session_options(3)
    opts = cls()
    assert isinstance(opts, ort.SessionOptions)
    assert opts.intra_op_num_threads == 3


def test_quad_slice_follows_the_reading_axis_of_tall_quads():
    # round-2, measured on a real page: rapidocr rot90s crops with
    # h/w >= 1.5 before recognition, so CTC timesteps run DOWN the quad,
    # not across — slicing tl->tr published 2px slivers at the wrong end
    from paperpin.backends.rapidocr_backend import _char_slices
    tall = [(700.0, 300.0), (750.0, 300.0), (750.0, 620.0), (700.0, 620.0)]
    first, last = _char_slices(tall, [(0.0, 0.1), (0.9, 1.0)])
    # first char near the TOP of the quad, spanning its width
    assert first[3] - first[1] < 100         # a slice of the height...
    assert first[2] - first[0] >= 45         # ...across the full width
    assert first[1] < last[1]                # reading order runs downward

    wide = [(100.0, 300.0), (420.0, 300.0), (420.0, 340.0), (100.0, 340.0)]
    first_w, last_w = _char_slices(wide, [(0.0, 0.1), (0.9, 1.0)])
    assert first_w[0] < last_w[0]            # horizontal reading order


def test_engine_state_is_a_single_atomic_pair():
    # round-2: _ENGINE was set before _ENGINE_STYLE; a second thread saw a
    # classic engine with style '' and the page silently read as empty
    import paperpin.backends.rapidocr_backend as rb
    engine, style = rb._load_engine()
    assert engine is not None and style in ("classic", "v2")


def test_unknown_backend_names_are_refused_by_name():
    """`backend="tesseract"` used to construct a backend no test ever ran.
    Removing it has to be a clear error, not an AttributeError later on."""
    import pytest

    from paperpin.backends.base import get_backend

    for name in ("tesseract", "paddle", ""):
        with pytest.raises(ValueError, match="unknown OCR backend"):
            get_backend(name)

    assert get_backend("auto").name == get_backend("rapidocr").name == "rapidocr"
