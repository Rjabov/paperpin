"""Extraction adapters (§4.4). An adapter turns a document into
{field: {"value": ..., "quote": ...}} (quote-then-extract — the quote is the
verbatim source snippet and is itself verified, §6.6.5).

`byo` takes existing JSON (the layer play — no model at all, E-40).
`gemini` calls the Gemini API. `openai` covers every OpenAI-compatible
endpoint (OpenAI, DeepSeek, OpenRouter, Ollama).
"""
from __future__ import annotations

from ..errors import ExtractionError

import json
from pathlib import Path
from typing import Any, Optional, Protocol

from ..intake.loader import Document
from ..types import FieldSpec


class Adapter(Protocol):
    name: str

    def extract(self, doc: Document, specs: dict[str, FieldSpec],
                prompt: Optional[str], page_texts: list[str],
                page_images: list) -> tuple[dict, dict]:
        """Returns (extraction, meta) — meta carries raw response, token usage."""
        ...


def get_adapter(model: str, api_key: Optional[str] = None,
                base_url: Optional[str] = None,
                timeout: float = 180.0) -> Adapter:
    if model.startswith(("gemini/", "gemini-")):
        from .gemini import GeminiAdapter
        return GeminiAdapter(model.split("/", 1)[-1], api_key=api_key,
                             timeout=timeout)
    if model.startswith(("openai/", "gpt-", "openrouter/", "deepseek/", "ollama/")):
        from .openai_compat import OpenAICompatAdapter
        return OpenAICompatAdapter(model, api_key=api_key, base_url=base_url,
                                   timeout=timeout)
    raise ValueError(
        f"unknown model {model!r} — use model='byo' with extraction=, "
        "'gemini/<model>', or an OpenAI-compatible id like 'openai/gpt-4o-mini'")


def load_byo_extraction(extraction: Any) -> dict:
    """Accept a dict, a JSON string, or a path to a JSON file."""
    def as_dict(parsed: Any, origin: str) -> dict:
        if not isinstance(parsed, dict):
            raise ExtractionError(
                f"{origin} holds {type(parsed).__name__} JSON — an "
                "extraction is an object of field -> value")
        return parsed

    if isinstance(extraction, dict):
        return extraction
    if isinstance(extraction, Path):
        if not extraction.exists():
            raise FileNotFoundError(f"no such extraction file: {extraction}")
        if extraction.is_dir():
            raise ExtractionError(f"{extraction} is a directory, not an extraction file")
        return as_dict(json.loads(extraction.read_text(encoding="utf-8")),
                       str(extraction))
    if isinstance(extraction, str):
        try:
            p = Path(extraction)
            exists = p.exists()
        except (OSError, ValueError):  # NUL bytes, absurd lengths
            exists = False
        if exists:
            if p.is_dir():
                raise ExtractionError(f"{p} is a directory, not an extraction file")
            return as_dict(json.loads(p.read_text(encoding="utf-8")), str(p))
        try:
            return as_dict(json.loads(extraction), "the extraction string")
        except json.JSONDecodeError as e:
            raise ValueError(
                f"extraction is neither an existing file nor valid JSON: "
                f"{extraction[:80]!r}") from e
    raise ExtractionError(
        f"cannot use {type(extraction).__name__} as an extraction — "
        "pass a dict of field -> value (or its JSON / a file path)")
