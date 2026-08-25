"""Adapters, with the network faked at `urllib.request.urlopen`.

These sit on the path of every model run and had almost no tests: what gets
sent, what gets parsed back, which failures retry, and — the two that matter
most — that a model id cannot escape into the request path and that one
provider's API key never travels to another provider's host.

No test here touches the network. Each fake urlopen asserts on the request it
was handed and returns a canned body.
"""
import email.message
import io
import json
import urllib.error
import urllib.request

import pytest
from PIL import Image

from paperpin.adapters.base import get_adapter
from paperpin.adapters.gemini import MAX_PAGES, GeminiAdapter
from paperpin.adapters.openai_compat import OpenAICompatAdapter
from paperpin.errors import ExtractionError


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, message="boom", retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    body = io.BytesIO(json.dumps({"error": {"message": message}}).encode("utf-8"))
    return urllib.error.HTTPError("https://x/y", code, "err", headers, body)


def gemini_body(text, prompt_tokens=11, output_tokens=7):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": prompt_tokens,
                              "candidatesTokenCount": output_tokens}}


def openai_body(text):
    return {"choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4}}


@pytest.fixture
def calls(monkeypatch):
    """Capture every request; each element is (url, headers, decoded payload)."""
    seen = []

    def record(responses):
        queue = list(responses)

        def fake_urlopen(req, timeout=None):
            seen.append((req.full_url, dict(req.headers),
                         json.loads(req.data.decode("utf-8"))))
            nxt = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(nxt, Exception):
                raise nxt
            return FakeResponse(nxt)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr("time.sleep", lambda *_: None)  # no real backoff
        return seen

    return record


# ----------------------------------------------------------------- gemini ---

def test_a_text_layer_document_ships_as_text_not_images(calls):
    seen = calls([gemini_body('{"total": {"value": "1", "quote": "1"}}')])
    adapter = GeminiAdapter("gemini-2.5-flash", api_key="k")

    extraction, meta = adapter.extract(None, {}, None, ["page one", "page two"], [])

    url, headers, payload = seen[0]
    parts = payload["contents"][0]["parts"]
    assert "gemini-2.5-flash:generateContent" in url
    assert headers["X-goog-api-key"] == "k"
    assert not any("inline_data" in p for p in parts), "text pages were rasterized"
    assert "PAGE BREAK" in parts[1]["text"]
    assert payload["generationConfig"]["temperature"] == 0
    assert extraction["total"]["value"] == "1"
    assert meta["token_usage"] == {"prompt_tokens": 11, "output_tokens": 7}


def test_image_pages_ship_as_inline_jpeg(calls):
    seen = calls([gemini_body("{}")])
    adapter = GeminiAdapter("gemini-2.5-flash", api_key="k")
    pages = [Image.new("RGB", (80, 100), "white")]

    adapter.extract(None, {}, None, [], pages)

    parts = seen[0][2]["contents"][0]["parts"]
    assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"


def test_pages_beyond_the_cap_are_dropped_and_reported(calls):
    seen = calls([gemini_body("{}")])
    adapter = GeminiAdapter("gemini-2.5-flash", api_key="k")
    pages = [Image.new("RGB", (20, 20)) for _ in range(MAX_PAGES + 3)]

    _, meta = adapter.extract(None, {}, None, [], pages)

    sent = sum(1 for p in seen[0][2]["contents"][0]["parts"] if "inline_data" in p)
    assert sent == MAX_PAGES
    assert meta["pages_truncated"] == 3, "silently dropped pages would look like " \
                                         "hallucinations on the fields they carried"


def test_a_field_the_model_skipped_still_gets_reported(calls):
    from paperpin.types import FieldSpec

    calls([gemini_body('{"total": {"value": "9", "quote": "9"}}')])
    adapter = GeminiAdapter("gemini-2.5-flash", api_key="k")
    specs = {"total": FieldSpec(name="total"), "iban": FieldSpec(name="iban")}

    extraction, _ = adapter.extract(None, specs, None, ["x"], [])

    assert extraction["iban"] == {"value": None, "quote": None}


def test_a_safety_block_is_an_error_naming_the_reason(calls):
    calls([{"candidates": [{"finishReason": "SAFETY"}]}])
    adapter = GeminiAdapter("gemini-2.5-flash", api_key="k")

    with pytest.raises(ExtractionError, match="SAFETY"):
        adapter.extract(None, {}, None, ["x"], [])


def test_a_rate_limit_retries_then_succeeds(calls):
    seen = calls([http_error(429, retry_after="3"),
                  gemini_body('{"total": {"value": "5"}}')])
    adapter = GeminiAdapter("gemini-2.5-flash", api_key="k")

    extraction, _ = adapter.extract(None, {}, None, ["x"], [])

    assert len(seen) == 2, "a 429 must be retried"
    assert extraction["total"]["value"] == "5"


def test_a_retry_after_date_does_not_crash_the_retry(calls):
    """Retry-After may be an HTTP-date (RFC 9110). int() on one used to raise
    inside the handler, turning a retryable 429 into a crash."""
    seen = calls([http_error(429, retry_after="Wed, 21 Oct 2026 07:28:00 GMT"),
                  gemini_body("{}")])
    adapter = GeminiAdapter("gemini-2.5-flash", api_key="k")

    adapter.extract(None, {}, None, ["x"], [])

    assert len(seen) == 2


def test_a_client_error_surfaces_the_provider_message(calls):
    calls([http_error(400, message="API key not valid")])
    adapter = GeminiAdapter("gemini-2.5-flash", api_key="k")

    with pytest.raises(ExtractionError, match="API key not valid"):
        adapter.extract(None, {}, None, ["x"], [])


def test_a_non_json_answer_is_retried_once_with_the_complaint(calls):
    seen = calls([gemini_body("here you go:"), gemini_body('{"total": {"value": "2"}}')])
    adapter = GeminiAdapter("gemini-2.5-flash", api_key="k")

    extraction, _ = adapter.extract(None, {}, None, ["x"], [])

    assert len(seen) == 2
    assert "ONLY the JSON object" in json.dumps(seen[1][2])
    assert extraction["total"]["value"] == "2"


def test_a_missing_key_says_what_to_do_instead_of_calling_out(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(ExtractionError, match="no Gemini API key"):
        GeminiAdapter("gemini-2.5-flash").extract(None, {}, None, ["x"], [])


@pytest.mark.parametrize("model", ["../../secrets", "a b", "m?key=leak", "m/../x"])
def test_a_model_id_cannot_escape_into_the_request_path(model):
    """The model name is interpolated into the URL. Anything but a bare
    identifier is refused before a request is ever built."""
    with pytest.raises(ExtractionError, match="invalid Gemini model id"):
        GeminiAdapter(model, api_key="k")


# --------------------------------------------------------- openai-compat ---

@pytest.mark.parametrize("model,host", [
    ("deepseek/deepseek-chat", "api.deepseek.com"),
    ("openrouter/anthropic/claude-3", "openrouter.ai"),
    ("ollama/llama3", "localhost:11434"),
    ("gpt-4o-mini", "api.openai.com"),
])
def test_each_prefix_goes_to_its_own_host(model, host, monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    assert host in OpenAICompatAdapter(model, api_key="k").base_url


def test_one_providers_key_never_travels_to_another_provider(calls, monkeypatch):
    """E-37. OPENAI_API_KEY is set for a great many developers; it must not
    ride along to a host the user did not choose. The adapter refuses to be
    built at all rather than borrowing it."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ExtractionError, match="DEEPSEEK_API_KEY"):
        OpenAICompatAdapter("deepseek/deepseek-chat")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-own")
    seen = calls([openai_body("{}")])
    OpenAICompatAdapter("deepseek/deepseek-chat").extract(None, {}, None, ["x"], [])

    url, headers, _ = seen[0]
    assert "deepseek" in url
    assert "sk-deepseek-own" in json.dumps(headers)
    assert "sk-openai-secret" not in json.dumps(headers)


def test_an_explicit_base_url_wins(calls):
    seen = calls([openai_body('{"total": {"value": "1"}}')])
    adapter = OpenAICompatAdapter("openai/gpt-4o-mini", api_key="k",
                                  base_url="http://127.0.0.1:8000/v1")

    adapter.extract(None, {}, None, ["x"], [])

    assert seen[0][0] == "http://127.0.0.1:8000/v1/chat/completions"


def test_a_refusal_with_no_content_is_an_error(calls):
    calls([{"choices": [{"message": {"content": None}}]}])
    adapter = OpenAICompatAdapter("openai/gpt-4o-mini", api_key="k")

    with pytest.raises(ExtractionError, match="no message content"):
        adapter.extract(None, {}, None, ["x"], [])


def test_an_object_wrapped_in_an_array_is_unwrapped(calls):
    """Models wrap the answer in a list often enough that salvage digs the
    object back out rather than failing the whole run."""
    calls([openai_body('[{"total": {"value": "1", "quote": "1"}}]')])
    adapter = OpenAICompatAdapter("openai/gpt-4o-mini", api_key="k")

    extraction, _ = adapter.extract(None, {}, None, ["x"], [])

    assert extraction["total"]["value"] == "1"


# ------------------------------------------------------------- selection ---

@pytest.mark.parametrize("model,expected", [
    ("gemini/gemini-2.5-flash", "gemini/gemini-2.5-flash"),
    ("gpt-4o-mini", "gpt-4o-mini"),
    ("deepseek/deepseek-chat", "deepseek/deepseek-chat"),
])
def test_get_adapter_routes_by_prefix(model, expected, monkeypatch):
    for var in ("GEMINI_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(var, "k")

    assert get_adapter(model).name == expected


def test_an_unknown_model_points_at_byo():
    with pytest.raises(ValueError, match="unknown model"):
        get_adapter("llama.cpp/mistral")
