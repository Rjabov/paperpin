"""The README demo must stay true: every claim in the hero screenshot is
asserted here. If the demo invoice, the demo extraction, or the matcher
drift apart, this fails before a visitor sees a stale story."""
import json
from pathlib import Path

import pytest

DEMO = Path(__file__).parent.parent / "fixtures" / "demo"

pytestmark = pytest.mark.skipif(not (DEMO / "demo_invoice.pdf").exists(),
                                reason="demo doc not generated")


def test_demo_extraction_grounds_as_the_readme_shows():
    from paperpin import ground
    extraction = json.loads((DEMO / "demo_extraction.json").read_text("utf-8"))
    result = ground(DEMO / "demo_invoice.pdf", extraction=extraction)

    statuses = {name: result[name].status for name in extraction}
    fabricated = statuses.pop("approved_by")
    assert fabricated == "not_found", "the planted fake must be flagged"
    assert set(statuses.values()) == {"verified"}, {
        k: v for k, v in statuses.items() if v != "verified"}
