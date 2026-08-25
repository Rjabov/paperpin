"""Shared test setup, and the layer taxonomy.

Every test carries exactly one layer marker, so a layer can be run on its own
(`pytest -m unit`, `pytest -m "contract or e2e"`). The mapping lives here
rather than in each file: one place to read, and a module that nobody has
placed in a layer is a collection error rather than a test quietly belonging
to nothing.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # makes `bench` importable in tests

#: layer -> the test modules in it.
#:
#:   unit         deterministic, hand-built input, no document read from disk
#:   integration  a real document goes through the pipeline, or the HTTP stack
#:   contract     the published surface: result JSON, CLI I/O, goldens, examples
#:   e2e          graded corpus gates — located rate, IoU, silent-wrong = 0
#:   security     each test pins a fix whose absence was demonstrated
#:
#: `slow` is orthogonal and stays declared in the module that needs it.
LAYERS = {
    "unit": [
        "test_aligner", "test_backends", "test_canon", "test_checksums",
        "test_dates", "test_numbers", "test_rows", "test_schemas",
        "test_segmentize", "test_tables", "test_transform", "test_verify_units",
    ],
    "integration": [
        "test_intake", "test_lab_api", "test_open_extraction",
        "test_pdf_render_lock",
    ],
    "contract": [
        "test_cli", "test_demo_doc", "test_examples", "test_golden_result",
        "test_page_images", "test_result_schema",
    ],
    "e2e": ["test_corpus_e2e", "test_degraded_e2e"],
    "security": ["test_security"],
}

_LAYER_OF = {module: layer
             for layer, modules in LAYERS.items() for module in modules}


def pytest_collection_modifyitems(config, items):
    unplaced = set()
    for item in items:
        layer = _LAYER_OF.get(item.path.stem)
        if layer is None:
            unplaced.add(item.path.stem)
        else:
            item.add_marker(getattr(pytest.mark, layer))
    if unplaced:
        raise pytest.UsageError(
            "these test modules are in no layer: " + ", ".join(sorted(unplaced))
            + " — add each to LAYERS in tests/conftest.py so `pytest -m <layer>` "
              "keeps meaning what it says")
