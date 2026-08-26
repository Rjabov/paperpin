"""The OCR segment cache.

A cache that returns the wrong entry produces pins that are subtly wrong on a
re-run — the exact failure paperpin exists to prevent, and the hardest kind to
notice because the first run was right. So: the key has to separate everything
that changes the answer, a damaged entry has to read as a miss rather than
propagate, and nothing here may ever be fatal — the cache is an optimisation.
"""
import os
import stat

import pytest

from paperpin import cache as segcache
from paperpin.types import Segment

SHA = "a" * 64
VARIANT = "rapidocr_v8_r2200d200"


@pytest.fixture(autouse=True)
def cache_home(tmp_path, monkeypatch):
    """Never touch the developer's real ~/.paperpin while testing."""
    monkeypatch.setenv("PAPERPIN_HOME", str(tmp_path))
    return tmp_path


def seg(text="2 424.54", **kw):
    base = dict(text=text, x0=10.0, top=20.0, x1=90.0, bottom=34.0,
                conf=0.97, page=0)
    base.update(kw)
    return Segment(**base)


def save(segments, meta=None, page=0, sha=SHA, variant=VARIANT):
    segcache.save_segments(sha, page, "rapidocr", variant, segments, meta or {})


def load(page=0, sha=SHA, variant=VARIANT):
    return segcache.load_segments(sha, page, "rapidocr", variant)


# ------------------------------------------------------------ round trip ---

def test_a_saved_page_comes_back_identical():
    original = [seg(), seg(text="SK73 1100", x0=11.5, conf=0.5)]

    save(original, {"orientation_k": 2})
    restored, meta = load()

    assert meta == {"orientation_k": 2}
    assert restored == original, "a segment changed shape through the cache"


def test_quads_and_char_boxes_survive_as_tuples():
    """rows.py indexes these positionally; JSON turns tuples into lists, and a
    list where a tuple belonged used to explode much later, far from here."""
    quad = [(1.0, 2.0), (3.0, 2.0), (3.0, 9.0), (1.0, 9.0)]
    boxes = [(1.0, 2.0, 2.0, 9.0), (2.0, 2.0, 3.0, 9.0)]

    save([seg(text="ab", quad=quad, char_boxes=boxes)])
    restored, _ = load()

    assert restored[0].quad == quad
    assert restored[0].char_boxes == boxes
    assert all(isinstance(q, tuple) for q in restored[0].quad)


def test_a_page_that_read_as_nothing_is_still_a_hit():
    """An empty page is a real OCR result. Caching it as a miss means every
    re-run pays for the same blank page again."""
    save([], {"orientation_k": 0})

    assert load() == ([], {"orientation_k": 0})


# -------------------------------------------------------------- the key ---

def test_nothing_that_changes_the_answer_shares_an_entry():
    save([seg(text="original")])

    assert load(page=1) is None, "a different page reused page 0's segments"
    assert load(sha="b" * 64) is None, "a different document reused this one's"
    assert load(variant="rapidocr_v9_r2200d200") is None, \
        "a geometry change reused the old raster's segments"
    assert load(variant="tesseract_v8_r2200d200") is None, \
        "a different backend reused this one's segments"


def test_a_hostile_variant_cannot_escape_the_cache_directory(cache_home):
    """`variant` is built from a backend's own `name`, which comes from a
    public Protocol — it must never be able to write outside the cache."""
    save([seg()], variant="../../../../etc/evil")

    segments_dir = (cache_home / "cache" / "segments").resolve()
    written = list(segments_dir.glob("*.json"))

    assert len(written) == 1
    # `..` survives (a dot is a legal filename character); what must not
    # survive is a separator, so the name stays flat and cannot traverse
    assert not {"/", os.sep}.intersection(written[0].name)
    assert written[0].resolve().parent == segments_dir


def test_a_long_sha_is_truncated_consistently():
    save([seg(text="x")])

    assert load(sha=SHA) is not None
    # only the first 32 chars key the entry; a collision there is a hit
    assert load(sha=SHA[:32] + "f" * 32) is not None


# ------------------------------------------------- damaged entries ---------

def test_an_absent_entry_is_a_miss_not_an_error():
    assert load() is None


@pytest.mark.parametrize("body", [
    "not json at all",
    "",
    '{"segments": "not a list", "meta": {}}',
    '{"segments": [], "meta": "not a dict"}',
    '{"segments": [{"text": 5, "x0": 1, "top": 1, "x1": 2, "bottom": 2}], "meta": {}}',
    '{"segments": [{"text": "a", "x0": "NaN-ish", "top": 1, "x1": 2, "bottom": 2}], "meta": {}}',
    '{"segments": [{"text": "a"}], "meta": {}}',
    '{"meta": {}}',
])
def test_a_damaged_entry_reads_as_a_miss(body, cache_home):
    """Every one of these used to reach rows.py and fail there instead. A
    cache is an optimisation; a broken one costs time, never correctness."""
    save([seg()])
    entry = next((cache_home / "cache" / "segments").glob("*.json"))
    entry.write_text(body, encoding="utf-8")

    assert load() is None


def test_a_directory_where_an_entry_belongs_is_a_miss(cache_home):
    save([seg()])
    entry = next((cache_home / "cache" / "segments").glob("*.json"))
    entry.unlink()
    entry.mkdir()

    assert load() is None


# ------------------------------------------------------ never fatal --------

def test_saving_into_an_unwritable_home_is_silent(monkeypatch):
    def refuse(*_a, **_kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(segcache, "write_private", refuse)

    save([seg()])                 # must not raise — grounding continues


def test_an_unusable_cache_home_does_not_stop_a_run(monkeypatch, tmp_path):
    monkeypatch.setattr(segcache, "_key_path",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))

    save([seg()])                 # must not raise


# ------------------------------------------------------------- privacy ----

def test_the_cache_holds_the_document_text_so_it_is_owner_only(cache_home):
    """Cached OCR is the document's full text. On a shared machine the default
    mode hands every local account someone's invoice."""
    save([seg(text="SK7311000000002612345678")])
    entry = next((cache_home / "cache" / "segments").glob("*.json"))

    assert "SK7311000000002612345678" in entry.read_text(encoding="utf-8")
    if os.name != "nt":  # POSIX permission bits are decorative on Windows
        assert stat.S_IMODE(entry.stat().st_mode) & 0o077 == 0
        assert stat.S_IMODE((cache_home).stat().st_mode) & 0o077 == 0


def test_private_dir_survives_a_filesystem_that_refuses_chmod(monkeypatch, tmp_path):
    def refuse(*_a, **_kw):
        raise OSError("FAT has no modes")

    monkeypatch.setattr("pathlib.Path.chmod", refuse)

    assert segcache.private_dir(tmp_path) == tmp_path   # best-effort, no raise


def test_cache_dir_follows_paperpin_home(cache_home):
    assert segcache.cache_dir() == cache_home / "cache" / "segments"
    assert segcache.cache_dir().is_dir()


def test_cache_dir_falls_back_to_the_home_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("PAPERPIN_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    assert segcache.cache_dir() == tmp_path / ".paperpin" / "cache" / "segments"


@pytest.mark.parametrize("raw,expected", [
    ("rapidocr_v8", "rapidocr_v8"),
    ("../etc/passwd", ".._etc_passwd"),
    ("a b\tc", "a_b_c"),
    ("naïve", "naïve"),        # str.isalnum() is Unicode-aware; letters are safe
])
def test_key_names_keep_only_safe_characters(raw, expected):
    assert segcache._safe(raw) == expected
