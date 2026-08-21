"""Price estimates — USD per 1M tokens (input, output), paid tier.

Snapshot of https://ai.google.dev/gemini-api/docs/pricing taken 2026-08-18.
Edit freely when prices move; unknown models show no estimate rather than a
wrong one. `-latest` aliases are mapped to the family's newest model (that is
what Google documents the alias to track) — estimates for them are marked
approximate in the UI.
"""
from __future__ import annotations

from typing import Optional

PRICES: dict[str, tuple[float, float]] = {
    # gemini 3.x (3.7/3.6 flash: promo pricing through 2026-12-31)
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.1-pro-preview": (2.00, 12.00),   # ≤200k-token prompts
    # gemini 2.5
    "gemini-2.5-pro": (1.25, 10.00),           # ≤200k-token prompts
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}

ALIASES: dict[str, str] = {
    "gemini-flash-latest": "gemini-3.7-flash",
    "gemini-flash-lite-latest": "gemini-3.5-flash-lite",
    "gemini-pro-latest": "gemini-3.1-pro-preview",
}


def estimate_usd(model: str, prompt_tokens: int, output_tokens: int
                 ) -> tuple[Optional[float], bool]:
    """Returns (estimate, is_approximate). Approximate = priced via an alias."""
    key = model.split("/", 1)[-1]
    approx = False
    if key in ALIASES:
        key = ALIASES[key]
        approx = True
    if key not in PRICES:
        return None, False
    i, o = PRICES[key]
    return round((prompt_tokens * i + output_tokens * o) / 1e6, 5), approx
