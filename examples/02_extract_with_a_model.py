"""Extract with a model, then ground every value it asserted.

Schema-free is the default: the model names the fields itself, paperpin
pins whatever it said. You only pass a schema when you want guided
recall (see 03_schemas.py).

Needs a key for the model you pick, e.g.:  export GEMINI_API_KEY=...
Run from the repo root:  python examples/02_extract_with_a_model.py
"""
import os
import sys

from paperpin import extract

if not os.environ.get("GEMINI_API_KEY"):
    sys.exit("set GEMINI_API_KEY first (or edit this file to use another "
             "model: openai/gpt-4.1-mini, openrouter/..., deepseek/..., "
             "ollama/llama3.2-vision, any OpenAI-compatible base_url)")

result = extract(
    "fixtures/demo/demo_invoice.pdf",
    model="gemini/gemini-2.5-flash",
    # everything below is optional:
    prompt="dates exactly as printed; item names verbatim",  # extra steering
    timeout=180.0,               # seconds, per model call
    # api_key="...",             # instead of the env var
    # base_url="http://localhost:11434/v1",  # OpenAI-compatible endpoints
)

for fr in result:
    print(f"{fr.name:28} {fr.status.value:14} {fr.evidence or ''}")

# what the run cost and how it went, straight from the result:
m = result.meta
print("\nadapter:", m.get("adapter"))
print("extract:", m.get("extract_seconds"), "s | ground:",
      m.get("ground_seconds"), "s")
print("token usage:", m.get("token_usage"))
# if the document had more pages than the adapter sends (12), you get:
print("pages truncated:", m.get("pages_truncated", 0))

# other model prefixes that work out of the box:
#   openai/gpt-4.1-mini     (or the bare gpt-... alias)
#   openrouter/anthropic/claude-sonnet-4.5
#   deepseek/deepseek-chat
#   ollama/llama3.2-vision  (local, no key)
