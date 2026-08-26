"""The result JSON is a contract other languages read; result.schema.json
is that contract written down.

Two gates. Real runs must validate against the schema — and the schema must
stay in step with the dataclasses that produce it, so adding a field to
`FieldResult` without describing it fails here rather than in someone's
TypeScript six months later.
"""
import json
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

import paperpin
from paperpin.types import Candidate, FieldResult, PageInfo, RESULT_SCHEMA

ROOT = Path(__file__).parent.parent
SCHEMA_PATH = Path(paperpin.__file__).parent / "result.schema.json"
DEMO = ROOT / "fixtures" / "demo"

jsonschema = pytest.importorskip("jsonschema")


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validator(schema):
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def _payload(pdf, extraction):
    from paperpin import ground
    return json.loads(ground(pdf, extraction=extraction).to_json())


@pytest.mark.skipif(not (DEMO / "demo_invoice.pdf").exists(),
                    reason="demo doc not generated")
def test_a_run_with_a_hallucination_validates(validator):
    extraction = json.loads((DEMO / "demo_extraction.json").read_text("utf-8"))
    payload = _payload(DEMO / "demo_invoice.pdf", extraction)

    validator.validate(payload)
    assert payload["fields"]["approved_by"]["status"] == "not_found"


@pytest.mark.skipif(not (DEMO / "demo_invoice.pdf").exists(),
                    reason="demo doc not generated")
def test_a_run_with_line_items_validates(validator):
    """Table cells flatten to `name[i].col`, a shape the schema has to allow
    as an ordinary field name."""
    payload = _payload(DEMO / "demo_invoice.pdf", {"line_items": [
        {"description": "Offset paper A3 100 g/m², natural white, 250-sheet ream",
         "qty": "6", "unit_price": "18.40"},
        {"description": "Kraft envelopes C4 self-seal, 90 g/m² ribbed, box of 250",
         "qty": "10", "unit_price": "31.25"},
    ]})

    validator.validate(payload)
    assert "line_items[0].qty" in payload["fields"]


def test_every_status_and_a_full_candidate_validate(validator):
    """The engine picks statuses per run, so no single document exercises all
    five. Serialize one of each, plus a candidate with every optional key
    populated, and put the whole schema through its branches."""
    from paperpin.types import GroundResult, Status

    candidate = Candidate(page=0, bbox=(0.1, 0.2, 0.3, 0.4), score=0.9,
                          evidence="146,14", exact=False, fused=True,
                          anchor="total", anchor_score=0.2, notes=["tied"])
    fields = {
        status.value: FieldResult(
            name=status.value, value="146,14", status=status, confidence=0.5,
            page=0, bbox=(0.1, 0.2, 0.3, 0.4), evidence="146,14",
            method="ocr", proof="checksum", anchor="total", quote="total 146,14",
            repaired_value="146.14", candidates=[candidate], notes=["note"])
        for status in Status
    }
    page = PageInfo(index=0, width=595.3, height=841.9, route="ocr",
                    dpi=150.0, px_width=1240, px_height=1754)

    payload = json.loads(
        GroundResult(fields=fields, pages=[page], source="x.pdf").to_json())

    validator.validate(payload)
    assert set(payload["summary"]) == {s.value for s in Status}


def test_the_payload_declares_the_schema_version(validator):
    from paperpin.types import GroundResult

    payload = json.loads(GroundResult(fields={}, pages=[], source="x").to_json())

    validator.validate(payload)
    assert payload["paperpin"]["schema"] == RESULT_SCHEMA


@pytest.mark.parametrize("dataclass_,pointer", [
    (FieldResult, "field"),
    (PageInfo, "page"),
    (Candidate, "candidate"),
])
def test_schema_describes_every_attribute_the_dataclass_emits(
        schema, dataclass_, pointer):
    """Drift guard: `additionalProperties: false` makes an undescribed
    attribute a validation failure, but only on a run that happens to set it.
    This catches it the moment the dataclass changes."""
    described = set(schema["$defs"][pointer]["properties"])
    emitted = {f.name for f in dataclass_fields(dataclass_)}

    assert emitted == described, (
        f"{dataclass_.__name__} and $defs/{pointer} disagree — "
        f"undescribed: {sorted(emitted - described)}, "
        f"stale in schema: {sorted(described - emitted)}")


def test_schema_lists_exactly_the_five_statuses(schema):
    from paperpin.types import Status

    assert set(schema["$defs"]["status"]["enum"]) == {s.value for s in Status}
