"""Gemini adapter — structured JSON output, quote-then-extract (§4.4).

Key handling (E-37): read from GEMINI_API_KEY / GOOGLE_API_KEY env (or an
explicit argument), sent only to Google's API endpoint, never logged, never
stored in results. 429/quota answered with visible queued retries (E-38),
malformed JSON salvaged tolerantly, schema mismatch retried once with the
validation error appended.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from ..intake.loader import Document
from ..errors import ExtractionError
from ..types import FieldSpec, FieldType

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_TRIES = 4
MAX_IMAGE_SIDE = 1600
MAX_PAGES = 12


def _prompt_template() -> str:
    return (Path(__file__).parent / "prompt.txt").read_text(encoding="utf-8")


_TYPE_HINTS = {
    FieldType.NUMBER: "a number as printed on the document",
    FieldType.PERCENT: "a percentage (number only, no % sign)",
    FieldType.DATE: "a date exactly as printed",
    FieldType.ID: "an identifier — copy exactly, keep all characters",
    FieldType.BLOCK: "a multi-line block — join lines with a single space",
    FieldType.TEXT: "text as printed",
}


# Schema-free mode: the model names the fields. Same value/quote contract,
# so grounding treats the result exactly like a guided extraction (E-40
# already infers specs for keys no schema covers).
_OPEN_FIELDS = """\
(no preset list) Identify EVERY meaningful printed field yourself:
- short snake_case names (invoice_number, supplier_name, total, iban, issue_date, …)
- repeating printed rows (line items, tax breakdown) become an ARRAY named for
  the group (line_items, tax_detail): one object per printed row, snake_case
  columns, values as printed — item rows only, never totals/subtotal rows.
  Return such arrays directly, not as value/quote objects.
- rule 5 then means: output exactly the field names YOU identified
- a field the paper does not print is simply omitted — a few real fields beat
  many invented ones"""


def build_prompt(specs: dict[str, FieldSpec], extra: Optional[str]) -> str:
    if not specs:
        return (_prompt_template()
                .replace("{fields_description}", _OPEN_FIELDS)
                .replace("{extra_instructions}",
                         f"\nAdditional instructions:\n{extra}" if extra else ""))
    lines = []
    for name, spec in specs.items():
        if spec.type == FieldType.TABLE and spec.columns:
            cols = ", ".join(spec.columns)
            lines.append(
                f"- {name}: an ARRAY with one object per printed line item, "
                f"each {{{cols}}} (values as printed, null for absent cells; "
                f"item rows only — never totals/subtotal rows). For {name}, "
                f"return the array directly, not a value/quote object.")
            continue
        hint = _TYPE_HINTS.get(spec.type, "text")
        lines.append(f"- {name}: {hint}")
    return (_prompt_template()
            .replace("{fields_description}", "\n".join(lines))
            .replace("{extra_instructions}",
                     f"\nAdditional instructions:\n{extra}" if extra else ""))


def response_schema(specs: dict[str, FieldSpec]) -> dict:
    quoted = {"type": "OBJECT",
              "properties": {"value": {"type": "STRING", "nullable": True},
                             "quote": {"type": "STRING", "nullable": True}},
              "required": ["value", "quote"]}
    props: dict[str, dict] = {}
    for name, spec in specs.items():
        if spec.type == FieldType.TABLE and spec.columns:
            props[name] = {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {c: {"type": "STRING", "nullable": True}
                               for c in spec.columns},
                "required": list(spec.columns)}}
        else:
            props[name] = dict(quoted)
    return {"type": "OBJECT", "properties": props, "required": list(specs)}


def salvage_json(text: str) -> dict:
    """Tolerant parse (E-38): fences and trailing prose stripped."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        t = t.split("\n", 1)[1] if "\n" in t else t
    start, end = t.find("{"), t.rfind("}")
    if start >= 0 and end > start:
        t = t[start:end + 1]
    return json.loads(t)


class GeminiAdapter:
    def __init__(self, model: str, api_key: Optional[str] = None,
                 status_cb: Optional[Callable[[str], None]] = None,
                 timeout: float = 180.0):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", model):
            raise ExtractionError(
                f"invalid Gemini model id {model!r} — the model name is placed "
                "in the request path and must be a bare identifier")
        self.model = model
        self.name = f"gemini/{model}"
        self._key = api_key or os.environ.get("GEMINI_API_KEY") \
            or os.environ.get("GOOGLE_API_KEY")
        self._timeout = timeout
        self._status = status_cb or (lambda msg: None)  # libraries stay silent

    def extract(self, doc: Document, specs: dict[str, FieldSpec],
                prompt: Optional[str], page_texts: list[str],
                page_images: list) -> tuple[dict, dict]:
        if not self._key:
            raise ExtractionError(
                "no Gemini API key — set GEMINI_API_KEY in the environment or "
                ".env (never commit it), or use model='byo' for offline grounding")

        parts: list[dict] = [{"text": build_prompt(specs, prompt)}]
        n_pages = max(len(page_texts), len(page_images))
        truncated = max(0, n_pages - MAX_PAGES)
        if page_texts and all(t.strip() for t in page_texts):
            body_text = "\n\n--- PAGE BREAK ---\n\n".join(page_texts[:MAX_PAGES])
            parts.append({"text": f"<<<DOCUMENT>>>\n{body_text}\n<<<END DOCUMENT>>>"})
        else:
            for img in page_images[:MAX_PAGES]:
                small = img.copy()
                small.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
                buf = io.BytesIO()
                small.convert("RGB").save(buf, format="JPEG", quality=85)
                parts.append({"inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(buf.getvalue()).decode("ascii")}})

        generation_config: dict = {
            "temperature": 0,
            "response_mime_type": "application/json",
        }
        if specs:  # open mode has no known keys to force
            generation_config["response_schema"] = response_schema(specs)
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation_config,
        }

        raw, usage = self._call_with_retries(payload)
        try:
            extraction = salvage_json(raw)
            if not isinstance(extraction, dict):
                raise json.JSONDecodeError("not an object", raw[:20] or ".", 0)
        except json.JSONDecodeError:
            self._status("response was not valid JSON — retrying once with the error")
            payload["contents"].append({"role": "model", "parts": [{"text": raw}]})
            payload["contents"].append({"role": "user", "parts": [{"text":
                "Your previous output was not valid JSON. Output ONLY the JSON object."}]})
            raw, usage2 = self._call_with_retries(payload)
            usage = {k: usage.get(k, 0) + usage2.get(k, 0) for k in set(usage) | set(usage2)}
            extraction = salvage_json(raw)  # let it raise loudly the second time
            if not isinstance(extraction, dict):
                raise ExtractionError(
                    f"{self.name} returned {type(extraction).__name__} "
                    "instead of a JSON object")

        meta = {"adapter": self.name, "_raw_response": raw, "token_usage": usage}
        if truncated:
            meta["pages_truncated"] = truncated
        for n in specs:
            extraction.setdefault(n, {"value": None, "quote": None})
        return extraction, meta

    def native_boxes(self, specs: dict[str, FieldSpec],
                     page_images: list) -> tuple[dict, dict]:
        """Ask the model for ITS OWN coordinates (arena fairness, E-39):
        box_2d in Gemini's documented [ymin, xmin, ymax, xmax] 0–1000
        normalized format, returned verbatim — never post-processed.
        """
        if not self._key:
            raise ExtractionError("no Gemini API key for the native-box pass")
        field_lines = "\n".join(f"- {name}" for name in specs)
        prompt = (
            "You are a document-understanding engine with grounding. For every field "
            "listed below, locate it on the page image(s) and return an object "
            '{"value": ..., "page": ..., "box_2d": [ymin, xmin, ymax, xmax]} where '
            "box_2d uses integers 0-1000 normalized to the page image, and page is "
            "the 0-based page index. Box the VALUE text only, tightly. "
            'If a field is absent, use {"value": null, "page": null, "box_2d": null}. '
            f"Output ONLY the JSON object.\n\nFields:\n{field_lines}\n")
        parts: list[dict] = [{"text": prompt}]
        for img in page_images[:MAX_PAGES]:
            small = img.copy()
            small.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            buf = io.BytesIO()
            small.convert("RGB").save(buf, format="JPEG", quality=85)
            parts.append({"inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(buf.getvalue()).decode("ascii")}})
        prop = {"type": "OBJECT",
                "properties": {
                    "value": {"type": "STRING", "nullable": True},
                    "page": {"type": "INTEGER", "nullable": True},
                    "box_2d": {"type": "ARRAY", "nullable": True,
                               "items": {"type": "INTEGER"}}},
                "required": ["value", "page", "box_2d"]}
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0,
                "response_mime_type": "application/json",
                "response_schema": {"type": "OBJECT",
                                    "properties": {n: dict(prop) for n in specs},
                                    "required": list(specs)},
            },
        }
        raw, usage = self._call_with_retries(payload)
        boxes = salvage_json(raw)
        meta = {"adapter": self.name, "_raw_response": raw, "token_usage": usage}
        return boxes, meta

    def _call_with_retries(self, payload: dict) -> tuple[str, dict]:
        url = f"{API_ROOT}/{self.model}:generateContent"
        body = json.dumps(payload).encode("utf-8")
        last_err: Optional[Exception] = None
        for attempt in range(MAX_TRIES):
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "x-goog-api-key": self._key})
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                try:
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError, TypeError):
                    reason = (data.get("candidates") or [{}])[0].get("finishReason")                         or data.get("promptFeedback", {}).get("blockReason") or "unknown"
                    raise ExtractionError(
                        f"Gemini returned no text (finish reason: {reason}) — "
                        "the request may have been safety-blocked or truncated")
                u = data.get("usageMetadata", {})
                usage = {"prompt_tokens": u.get("promptTokenCount", 0),
                         "output_tokens": u.get("candidatesTokenCount", 0)}
                return text, usage
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (429, 500, 502, 503) and attempt < MAX_TRIES - 1:
                    # Retry-After may be seconds OR an HTTP-date (RFC 9110);
                    # int() on a date raised inside this handler. Parse
                    # defensively and clamp — a provider must not park us for
                    # hours.
                    try:
                        hinted = int(e.headers.get("Retry-After", 0))
                    except (TypeError, ValueError):
                        hinted = 0
                    delay = min(60, hinted or (2 ** attempt * 3))
                    self._status(f"HTTP {e.code} — retrying in {delay}s "
                                 f"({attempt + 1}/{MAX_TRIES - 1})")
                    time.sleep(delay)
                    continue
                detail = ""
                try:
                    detail = json.loads(e.read().decode("utf-8"))["error"]["message"]
                except Exception:
                    pass
                raise ExtractionError(f"Gemini API error {e.code}: {detail or e.reason}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                if attempt < MAX_TRIES - 1:
                    self._status(f"network error ({e}) — retrying")
                    time.sleep(2 ** attempt * 2)
                    continue
        raise ExtractionError(f"Gemini API unreachable after {MAX_TRIES} tries: {last_err}")
