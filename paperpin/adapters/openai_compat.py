"""One adapter for every OpenAI-compatible endpoint (§4.4): OpenAI, DeepSeek,
OpenRouter, Ollama. JSON-object mode + explicit instructions (json_schema
support varies too much across providers to rely on).
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional

from urllib.parse import urlsplit

from ..intake.loader import Document
from ..errors import ExtractionError
from ..types import FieldSpec
from .gemini import MAX_IMAGE_SIDE, MAX_PAGES, build_prompt, salvage_json


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return (host in ("localhost", "::1", "0.0.0.0")
            or host.startswith("127.")
            or host.endswith(".localhost"))

DEFAULT_BASES = {
    "openai": "https://api.openai.com/v1",
    "gpt": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}
# each provider reads its own key; OPENAI_API_KEY must never ride along to a
# different host the user did not choose (E-37)
KEY_ENVS = {
    "openai": "OPENAI_API_KEY", "gpt": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY", "openrouter": "OPENROUTER_API_KEY",
}
MAX_TRIES = 4


class OpenAICompatAdapter:
    def __init__(self, model: str, api_key: Optional[str] = None,
                 base_url: Optional[str] = None, timeout: float = 180.0):
        self._timeout = timeout
        prefix, _, bare = model.partition("/")
        self.model = bare or model
        self.name = model
        env_base = os.environ.get("OPENAI_BASE_URL") if prefix in ("openai", "gpt")             else None  # an explicit deepseek/... prefix must go where it says
        self.base_url = (base_url or env_base
                         or DEFAULT_BASES.get(prefix, DEFAULT_BASES["openai"])).rstrip("/")
        key_env = KEY_ENVS.get(prefix)
        if prefix == "ollama":
            self._key = api_key or "ollama"  # local server, dummy bearer
        else:
            self._key = api_key or os.environ.get(key_env or "OPENAI_API_KEY")
            if not self._key:
                raise ExtractionError(
                    f"no API key for {model!r} — set {key_env or 'OPENAI_API_KEY'} "
                    "in the environment or .env (never commit it)")
            # a real key must never ride a plaintext channel to a remote host:
            # a Bearer token over http:// to anything but loopback is an
            # exfiltration hazard (a typo'd or hostile base_url). Loopback
            # (Ollama, a local proxy) is fine; everything else must be https.
            if self.base_url.startswith("http://") and not _is_loopback(self.base_url):
                raise ExtractionError(
                    f"refusing to send the API key for {model!r} over plaintext "
                    f"HTTP to {urlsplit(self.base_url).hostname!r} — use https, or "
                    "a loopback address for a local endpoint")

    def extract(self, doc: Document, specs: dict[str, FieldSpec],
                prompt: Optional[str], page_texts: list[str],
                page_images: list) -> tuple[dict, dict]:
        content: list[dict] = [{"type": "text", "text": build_prompt(specs, prompt)}]
        n_pages = max(len(page_texts), len(page_images))
        truncated = max(0, n_pages - MAX_PAGES)
        if page_texts and all(t.strip() for t in page_texts):
            joined = "\n\n--- PAGE BREAK ---\n\n".join(page_texts[:MAX_PAGES])
            content.append({"type": "text",
                            "text": f"<<<DOCUMENT>>>\n{joined}\n<<<END DOCUMENT>>>"})
        else:
            for img in page_images[:MAX_PAGES]:
                small = img.copy()
                small.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
                buf = io.BytesIO()
                small.convert("RGB").save(buf, format="JPEG", quality=85)
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                content.append({"type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

        payload = {"model": self.model, "temperature": 0,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "user", "content": content}]}

        url = f"{self.base_url}/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        last_err: Optional[Exception] = None
        for attempt in range(MAX_TRIES):
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self._key}"})
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                try:
                    raw = data["choices"][0]["message"]["content"]
                    if raw is None:
                        raise KeyError("content")
                except (KeyError, IndexError, TypeError):
                    raise ExtractionError(
                        f"{self.name} returned no message content — refusal "
                        "or truncated response") from None
                u = data.get("usage", {})
                extraction = salvage_json(raw)
                if not isinstance(extraction, dict):
                    raise ExtractionError(
                        f"{self.name} returned {type(extraction).__name__} "
                        "instead of a JSON object")
                for n in specs:
                    extraction.setdefault(n, {"value": None, "quote": None})
                meta = {"adapter": self.name, "_raw_response": raw,
                        "token_usage": {"prompt_tokens": u.get("prompt_tokens", 0),
                                        "output_tokens": u.get("completion_tokens", 0)}}
                if truncated:
                    meta["pages_truncated"] = truncated
                return extraction, meta
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (429, 500, 502, 503) and attempt < MAX_TRIES - 1:
                    time.sleep(2 ** attempt * 3)
                    continue
                raise ExtractionError(f"{self.name} API error {e.code}: {e.reason}") from e
            except json.JSONDecodeError as e:
                raise ExtractionError(
                    f"{self.name} answered with something that is not JSON — "
                    "likely a refusal or a proxy page") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                if attempt < MAX_TRIES - 1:
                    time.sleep(2 ** attempt * 2)
                    continue
        raise ExtractionError(f"{self.name} unreachable after {MAX_TRIES} tries: {last_err}")
