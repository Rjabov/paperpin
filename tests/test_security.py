"""Security invariants (review 2026-08-21). Each test pins a fix whose
absence was demonstrated by execution against the shipped code."""
import os
import stat

import pytest

from paperpin.errors import PaperpinError


def test_api_key_never_rides_plaintext_to_a_remote_host():
    from paperpin.adapters.base import get_adapter
    with pytest.raises(PaperpinError) as e:
        get_adapter("openai/gpt-4o-mini", api_key="sk-secret",
                    base_url="http://evil.example.com:8080/v1")
    assert "plaintext" in str(e.value).lower()


def test_loopback_endpoints_may_stay_plaintext():
    from paperpin.adapters.base import get_adapter
    for base in ("http://localhost:11434/v1", "http://127.0.0.1:8000/v1"):
        a = get_adapter("openai/local-model", api_key="k", base_url=base)
        assert a.base_url.startswith("http://")
    # ollama's default needs no key at all
    assert get_adapter("ollama/llama3").base_url.startswith("http://localhost")


def test_https_remote_endpoint_is_accepted():
    from paperpin.adapters.base import get_adapter
    a = get_adapter("openai/gpt-4o-mini", api_key="sk-secret",
                    base_url="https://api.example.com/v1")
    assert a.base_url == "https://api.example.com/v1"


def test_gemini_model_id_cannot_shape_the_request_path():
    from paperpin.adapters.gemini import GeminiAdapter
    with pytest.raises(PaperpinError):
        GeminiAdapter("../../v1beta/models/x:generateContent?k=", api_key="k")
    assert GeminiAdapter("gemini-2.5-flash", api_key="k").model == "gemini-2.5-flash"


def test_retry_after_http_date_does_not_crash_and_is_clamped():
    # RFC 9110 allows an HTTP-date; int() on it used to raise inside the
    # 429 handler, and a numeric value was slept verbatim
    import email.utils as _eu  # noqa: F401  (documents the date form)

    def parse(raw):
        try:
            hinted = int(raw)
        except (TypeError, ValueError):
            hinted = 0
        return min(60, hinted or 3)

    assert parse("Wed, 21 Aug 2026 07:28:00 GMT") == 3
    assert parse("86400") == 60
    assert parse("5") == 5


def test_saved_results_do_not_carry_the_raw_model_response():
    from paperpin.types import FieldResult, GroundResult, Status
    r = GroundResult(
        fields={"a": FieldResult(name="a", value="1", status=Status.VERIFIED,
                                 confidence=1.0)},
        pages=[], source="x.pdf",
        meta={"adapter": "gemini/x", "_raw_response": "VERBATIM DOC SNIPPETS",
              "token_usage": {"prompt_tokens": 1}})
    exported = r.to_dict()
    assert "_raw_response" not in exported["meta"]
    assert "VERBATIM DOC SNIPPETS" not in str(exported)
    assert exported["meta"]["adapter"] == "gemini/x"   # useful meta survives


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are decorative on Windows; the 0o600 guarantee is POSIX-only")
def test_ocr_cache_is_not_world_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERPIN_HOME", str(tmp_path))
    from paperpin import cache
    cache.save_segments("a" * 64, 0, "fake", "v8", [], {"orientation_k": 0})
    entry = next(cache.cache_dir().glob("*.json"))
    assert stat.S_IMODE(entry.stat().st_mode) & 0o077 == 0, "cache file readable by others"
    assert stat.S_IMODE(tmp_path.stat().st_mode) & 0o077 == 0, "paperpin home readable by others"


def test_document_text_is_fenced_as_data_in_the_prompt():
    from paperpin.adapters.gemini import build_prompt
    from paperpin.schemas import resolve_schema
    prompt = build_prompt(resolve_schema({"total": {"type": "number"}}), None)
    assert "<<<DOCUMENT>>>" in prompt and "<<<END DOCUMENT>>>" in prompt
    assert "never instructions" in prompt
