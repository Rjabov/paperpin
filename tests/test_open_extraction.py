"""Schema-free extraction: with no field specs the adapter asks the model to
identify every printed field itself, and stops forcing a fixed response
schema (there are no known keys to force)."""
import pytest

pytest.importorskip("PIL")

from paperpin.adapters.gemini import GeminiAdapter, build_prompt, response_schema


def test_open_prompt_when_no_specs():
    p = build_prompt({}, None)
    assert "snake_case" in p
    assert "line item" in p.lower()
    assert "{fields_description}" not in p


def test_guided_prompt_unchanged(monkeypatch):
    from paperpin.schemas import resolve_schema
    p = build_prompt(resolve_schema("invoice"), None)
    assert "- total:" in p
    assert "snake_case" not in p


def _extract_payload(monkeypatch, specs):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    adapter = GeminiAdapter("gemini-flash-latest")
    seen = {}

    def fake_call(payload):
        seen.update(payload)
        return '{"total": {"value": "1", "quote": "1"}}', {}

    monkeypatch.setattr(adapter, "_call_with_retries", fake_call)
    adapter.extract(None, specs, None, ["page text"], [])
    return seen


def test_response_schema_omitted_when_open(monkeypatch):
    payload = _extract_payload(monkeypatch, {})
    assert "response_schema" not in payload["generationConfig"]
    assert payload["generationConfig"]["response_mime_type"] == "application/json"


def test_response_schema_forced_when_guided(monkeypatch):
    from paperpin.schemas import resolve_schema
    payload = _extract_payload(monkeypatch, resolve_schema("invoice"))
    assert "response_schema" in payload["generationConfig"]
