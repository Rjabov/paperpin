"""The knobs: backend, cache, progress, threads, endpoints. Plus the CLI.

Run from the repo root:  python examples/05_options_and_cli.py
"""
from paperpin import ground

# --- progress: watch the pipeline stages ---------------------------------
# Any callable(stage, phase, info). Exceptions in it never break a run.
def progress(stage: str, phase: str, info: dict) -> None:
    if phase == "start":
        print(f"  [{stage}] ...", flush=True)

result = ground(
    "fixtures/demo/demo_invoice.pdf",
    extraction={"total_due": "2 424.54"},
    backend="auto",      # OCR backend for pages without a text layer.
                         # "auto" == "rapidocr" today; PDF text layers are
                         # used directly and never touch OCR.
    use_cache=True,      # OCR text cache under ~/.paperpin/cache/ makes
                         # re-runs on the same file instant. False = always
                         # re-read. CLI flag: --no-cache
    progress=progress,
)
print("done:", result.counts(), "in", result.meta.get("ground_seconds"), "s")

# --- environment knobs ---------------------------------------------------
# PAPERPIN_OCR_THREADS=1   pin OCR to one core (~2x slower, machine stays
#                          responsive). Pair with `nice -n 19` for a worker
#                          that grounds invisibly in the background.
# GEMINI_API_KEY etc.      adapter keys. The CLI also loads a local .env;
#                          as a library, export the var or pass api_key=.

# --- OpenAI-compatible endpoints -----------------------------------------
# Anything speaking the OpenAI chat API works via base_url:
# extract("doc.pdf", model="openai/qwen2.5-vl",
#         base_url="http://localhost:11434/v1")   # e.g. Ollama, vLLM, LM Studio

# --- the same pipeline from the shell ------------------------------------
# paperpin ground  doc.pdf --extraction out.json -o result.json \
#                  --overlay proof.png --view proof.html
# paperpin extract doc.pdf --model gemini/gemini-2.5-flash -o result.json
# paperpin extract doc.pdf --schema invoice --model byo --extraction out.json
# paperpin overlay doc.pdf result.json -o proof.png --page 0
# paperpin view    doc.pdf result.json -o proof.html
# paperpin lab --port 8377          # the local demo UI
# paperpin version
