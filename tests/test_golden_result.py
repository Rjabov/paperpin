"""Golden results: committed samples of exactly what the engine emits.

The JSON schema says what shape is *legal*; these say what the engine actually
produces for two known documents, down to every bbox. A matcher change that
quietly moves a box shows up here as a diff, and the files double as real
input for consumers in other languages, which is why they are committed rather
than generated at test time.

Volatile parts are canonicalized away (see `_canonical`) so the files change
only when the *result* changes — never on a release or a slow machine.

Regenerate after an intentional change, then read the diff before committing:

    PAPERPIN_UPDATE_GOLDEN=1 pytest tests/test_golden_result.py
"""
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
DEMO = ROOT / "fixtures" / "demo"
GOLDEN = ROOT / "fixtures" / "golden"

pytestmark = pytest.mark.skipif(not (DEMO / "demo_invoice.pdf").exists(),
                                reason="demo doc not generated")

UPDATING = os.environ.get("PAPERPIN_UPDATE_GOLDEN") == "1"

LINE_ITEMS = {"line_items": [
    {"description": "Offset paper A3 100 g/m², natural white, 250-sheet ream",
     "qty": "6", "unit_price": "18.40"},
    {"description": "Kraft envelopes C4 self-seal, 90 g/m² ribbed, box of 250",
     "qty": "10", "unit_price": "31.25"},
]}


def _cases():
    demo = json.loads((DEMO / "demo_extraction.json").read_text("utf-8"))
    return {"demo": demo, "line_items": LINE_ITEMS}


#: Coordinates round to this many places before comparison. Real drift moves a
#: box by orders of magnitude more than this; last-bit float differences
#: between CI platforms move it by orders of magnitude less.
PLACES = 6


def _round(value):
    if isinstance(value, float):
        return round(value, PLACES)
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v) for v in value]
    return value


def _canonical(payload: dict) -> dict:
    """Drop what changes without the result changing: the library version, the
    absolute path the run happened to use, every timing, and float noise below
    what any real change would produce."""
    payload = json.loads(json.dumps(payload))  # never mutate the caller's dict
    payload["paperpin"]["version"] = "<version>"
    payload["source"] = Path(payload["source"]).name
    payload["meta"].pop("ground_seconds", None)
    payload["meta"].pop("profile", None)
    return _round(payload)


@pytest.mark.parametrize("case", sorted(_cases()))
def test_engine_still_produces_the_golden_result(case):
    from paperpin import ground

    result = ground(DEMO / "demo_invoice.pdf", extraction=_cases()[case])
    actual = _canonical(json.loads(result.to_json()))
    path = GOLDEN / f"{case}.json"

    if UPDATING:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        pytest.skip(f"rewrote {path.relative_to(ROOT)}")

    expected = json.loads(path.read_text("utf-8"))
    assert actual == expected, (
        f"{case}: the engine no longer produces fixtures/golden/{case}.json. "
        "If the change is intentional, regenerate with "
        "PAPERPIN_UPDATE_GOLDEN=1 and review the diff.")


@pytest.mark.skipif(UPDATING, reason="regenerating")
@pytest.mark.parametrize("case", sorted(_cases()))
def test_golden_files_validate_against_the_published_schema(case):
    """A consumer in another language reads these files, so they have to obey
    the schema that same consumer generates its types from."""
    jsonschema = pytest.importorskip("jsonschema")

    schema = json.loads((ROOT / "docs" / "result.schema.json").read_text("utf-8"))
    payload = json.loads((GOLDEN / f"{case}.json").read_text("utf-8"))

    jsonschema.validators.validator_for(schema)(schema).validate(payload)


@pytest.mark.skipif(UPDATING, reason="regenerating")
def test_the_golden_demo_still_tells_the_readme_story():
    """Guard the guard: a golden nobody reads could drift into agreeing with a
    broken engine. These two numbers are the README's claim."""
    payload = json.loads((GOLDEN / "demo.json").read_text("utf-8"))

    assert payload["summary"] == {"verified": 19, "not_found": 1}
    assert payload["fields"]["approved_by"]["bbox"] is None
