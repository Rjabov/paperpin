"""End-to-end gates on the synthetic corpus (HANDOVER §9.2).

Text layer: every field located, zero silent-wrong, every planted fake caught.
The degraded OCR tiers run in test_degraded_e2e.py (slower, marked)."""
import json
from pathlib import Path

import pytest

CORPUS = Path(__file__).parent.parent / "fixtures" / "corpus"

pytestmark = pytest.mark.skipif(not CORPUS.exists(),
                                reason="corpus not generated")


def _docs():
    for meta_path in sorted(CORPUS.glob("inv_*.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        yield meta_path.stem, meta


@pytest.mark.parametrize("name,meta", list(_docs()), ids=lambda v: v if isinstance(v, str) else "")
def test_textlayer_full_marks(name, meta):
    from bench.evaluate import evaluate_doc
    r = evaluate_doc(CORPUS / f"{name}.pdf", meta["extraction"], meta["truth"])
    assert r["fake_caught"], f"{name}: planted hallucination NOT caught"
    assert r["n_silent_wrong"] == 0, f"{name}: silent wrong locations: " + str(
        {k: v for k, v in r["fields"].items() if v.get("wrong_location")})
    assert r["located_rate"] == 1.0, f"{name}: missed fields: " + str(
        [k for k, v in r["fields"].items() if not v["located"]])


def test_overlay_ink_under_box_e36(tmp_path):
    """Pixel-proof: the matched text's ink must sit inside the drawn box."""
    import numpy as np
    from paperpin import ground

    name, meta = next(iter(_docs()))
    result = ground(str(CORPUS / f"{name}.pdf"),
                    extraction={"total": meta["extraction"]["total"]},
                    schema="invoice")
    fr = result["total"]
    assert fr.bbox is not None
    img = result.meta["_page_image_provider"](0).convert("L")
    W, H = img.size
    x0, y0, x1, y1 = (int(fr.bbox[0] * W), int(fr.bbox[1] * H),
                      int(fr.bbox[2] * W), int(fr.bbox[3] * H))
    crop = np.asarray(img)[max(0, y0):y1 + 1, max(0, x0):x1 + 1]
    assert crop.size > 0 and crop.min() < 128, \
        "no dark ink inside the pinned box — overlay would miss the text"


def test_textlayer_result_carries_no_ocr_stats():
    # meta.ocr reports OCR read-coverage honesty stats; a pure text-layer doc
    # has no OCR pages, so the key must be absent (not an empty stub)
    import json
    from paperpin.api import ground
    metas = sorted(CORPUS.glob("*.json"))
    m = json.loads(metas[0].read_text(encoding="utf-8"))
    name = metas[0].stem
    result = ground(str(CORPUS / f"{name}.pdf"), extraction=m["extraction"])
    assert "ocr" not in result.meta
    assert all(p.route == "textlayer" for p in result.pages)


def test_ground_never_mutates_the_callers_extraction():
    from paperpin import ground
    mine = {"invoice_number": "20260461", "line_items": [{"qty": "1"}],
            "tags": ["alpha", "beta"]}
    snapshot = {"invoice_number": "20260461", "line_items": [{"qty": "1"}],
                "tags": ["alpha", "beta"]}
    ground("fixtures/corpus/inv_en_left.pdf", extraction=mine)
    assert mine == snapshot


def test_scalar_list_values_get_statuses():
    from paperpin import ground
    res = ground("fixtures/corpus/inv_en_left.pdf",
                 extraction={"tags": ["alpha", "beta"]})
    assert "tags[0]" in res and "tags[1]" in res  # every element has a status


def test_empty_list_field_keeps_a_status():
    # E-P1-2 (round-3): extraction {"line_items": []} vanished from the
    # result entirely — "no line items" is an assertion and gets a status
    import paperpin

    res = paperpin.ground("fixtures/corpus/inv_en_left.pdf",
                          extraction={"total": "146,14", "line_items": []})
    assert "line_items" in res
    assert res["line_items"].status.value == "not_present"


def test_saved_result_round_trips_its_meta(tmp_path):
    # E-P2-9 (round-3): from_dict never read data["meta"] — reload lost the
    # honesty metadata (backend, ocr stats, pages_truncated)
    import json

    import paperpin
    from paperpin.types import GroundResult

    res = paperpin.ground("fixtures/corpus/inv_en_left.pdf",
                          extraction={"total": "146,14"})
    p = tmp_path / "r.json"
    res.save(str(p))
    back = GroundResult.from_dict(json.loads(p.read_text()))
    assert back.meta.get("backend") == res.meta.get("backend")
    assert "ground_seconds" in back.meta
