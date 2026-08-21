"""OCR-route gates on the degradation matrix (HANDOVER §9.2).

The hard promise, on EVERY tier: silent-wrong = 0. A verified status with a
wrong box is the one failure mode the product must never have; a lost or
uncertain field must come back flagged (low_confidence / ambiguous /
not_found), never confidently wrong.

Located-rate thresholds per tier (set from the current baseline, never
lowered casually):
  clean render ≥ 0.95 · rot270 ≥ 0.90 · blur_jpeg30 ≥ 0.70 · photo_sim ≥ 0.70
"""
import json
from pathlib import Path

import pytest

DEGRADED = Path(__file__).parent.parent / "fixtures" / "degraded"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not DEGRADED.exists(), reason="degraded corpus not generated"),
]

TIERS = {"clean": 0.95, "rot270": 0.90, "blur_jpeg30": 0.70, "photo_sim": 0.70}


def _variants():
    if not DEGRADED.exists():
        return
    manifest = json.loads((DEGRADED / "manifest.json").read_text())
    for entry in manifest:
        yield entry["name"], entry["variant"]


@pytest.mark.parametrize("name,variant", list(_variants()),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_degraded_tier(name, variant):
    from bench.evaluate import evaluate_doc
    meta = json.loads((DEGRADED / f"{name}.json").read_text(encoding="utf-8"))
    r = evaluate_doc(DEGRADED / f"{name}.jpg", meta["extraction"], meta["truth"])
    assert r["fake_caught"], f"{name}: planted hallucination NOT caught"
    assert r["n_silent_wrong"] == 0, \
        f"{name}: VERIFIED status on a wrong location — the forbidden failure: " + str(
            {k: v for k, v in r["fields"].items()
             if v.get("wrong_location") and v["status"] == "verified"})
    assert r["located_rate"] >= TIERS[variant], \
        f"{name}: located {r['located_rate']:.2f} < {TIERS[variant]} — missed: " + str(
            [k for k, v in r["fields"].items() if not v["located"]])
