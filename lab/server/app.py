"""The Lab backend — FastAPI wrapping the paperpin core (HANDOVER §5).

Local-only by design: binds 127.0.0.1, zero telemetry, documents and results
in ~/.paperpin/lab/. Only explicit model-run requests reach a provider, and
the UI says so at the model picker (E-43).
"""
from __future__ import annotations

import io
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from paperpin.env import load_dotenv

from . import db, runner

app = FastAPI(title="paperpin lab", docs_url="/api/docs", openapi_url="/api/openapi.json")
load_dotenv()

# Binding 127.0.0.1 keeps remote packets out; it does NOT keep a browser out.
# A page that resolves its own hostname to 127.0.0.1 (DNS rebinding) becomes
# same-origin with the Lab and can read every document, page image and result
# — and spend the user's API credits. The Host header is what distinguishes
# "the user typed localhost" from "a rebound attacker hostname".
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}

# The Host/Origin guard stops browsers; it does not stop another local
# process. A per-start token (printed in the startup URL) closes that hole.
# Accepted as a query param too because <img src> cannot send headers.
LAB_TOKEN = os.environ.get("PAPERPIN_LAB_TOKEN") or secrets.token_urlsafe(32)


def _token_ok(request: Request) -> bool:
    supplied = (request.headers.get("x-lab-token")
                or request.query_params.get("token") or "")
    return secrets.compare_digest(supplied, LAB_TOKEN)


def _host_allowed(raw_host: str) -> bool:
    host = raw_host.split(",")[0].strip().lower()
    if host.startswith("[") and "]" in host:          # [::1]:8000
        host = host[: host.index("]") + 1]
    elif ":" in host:                                  # localhost:8000
        host = host.rsplit(":", 1)[0]
    return host in ALLOWED_HOSTS


@app.middleware("http")
async def guard_local_only(request: Request, call_next):
    if not _host_allowed(request.headers.get("host", "")):
        return JSONResponse(
            {"detail": "paperpin lab serves localhost only — this request "
                       "arrived with a foreign Host header"}, status_code=421)
    origin = request.headers.get("origin")
    if origin and not _host_allowed(origin.split("//", 1)[-1]):
        return JSONResponse(
            {"detail": "cross-origin requests are not accepted"}, status_code=403)
    if request.url.path.startswith("/api/") and not _token_ok(request):
        return JSONResponse(
            {"detail": "missing or wrong lab token — start the lab and open "
                       "the printed URL"}, status_code=401)
    return await call_next(request)

MAX_UPLOAD_MB = 60          # E-6/E-42: friendly cap, not a crash
BIG_FILE_WARN_MB = 20


# ------------------------------------------------------------- documents ---

@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(400, "file is empty (zero bytes)")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"file exceeds {MAX_UPLOAD_MB}MB — split the "
                                 "document or lower the scan resolution")
    from paperpin.intake.loader import load_document
    try:
        doc = load_document(data, filename=file.filename or "upload")
    except ValueError as e:
        raise HTTPException(422, str(e))

    existing = db.query_one("SELECT * FROM documents WHERE sha256=?", (doc.sha256,))
    if existing:
        return _doc_payload(existing)

    ext = Path(file.filename or "doc").suffix or (".pdf" if doc.kind == "pdf" else ".img")
    stored = db.lab_home() / "docs" / f"{doc.sha256}{ext}"
    stored.write_bytes(data)
    pages_meta = [p.info().to_dict() for p in doc.pages]
    doc.close()

    doc_id = db.execute(
        "INSERT INTO documents(filename,sha256,mime,pages,bytes_path,pages_json,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (file.filename, doc.sha256, file.content_type, len(pages_meta),
         str(stored), json.dumps(pages_meta), db.now()))
    row = db.query_one("SELECT * FROM documents WHERE id=?", (doc_id,))
    payload = _doc_payload(row)
    if len(data) > BIG_FILE_WARN_MB * 1024 * 1024 or len(pages_meta) > 20:
        payload["warning"] = (f"large document ({len(data) // (1024 * 1024)}MB, "
                              f"{len(pages_meta)} pages) — OCR runs may take a while")
    return payload


@app.get("/api/documents")
def list_documents():
    return [_doc_payload(r) for r in
            db.query("SELECT * FROM documents ORDER BY created_at DESC")]


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: int):
    row = db.query_one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if row is None:
        raise HTTPException(404, "no such document")
    return _doc_payload(row)


@app.get("/api/documents/{doc_id}/pages/{page}.jpg")
def page_raster(doc_id: int, page: int, width: int = 1400):
    row = db.query_one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if row is None:
        raise HTTPException(404, "no such document")
    width = max(100, min(2400, width))
    cache = db.lab_home() / "pages" / f"{row['sha256']}_p{page}_w{width}.jpg"
    if not cache.exists():
        from paperpin.intake.loader import load_document
        doc = load_document(row["bytes_path"])
        if page >= len(doc.pages):
            raise HTTPException(404, "no such page")
        img = doc.pages[page].raster()
        if img.width > width:
            ratio = width / img.width
            img = img.resize((width, int(img.height * ratio)))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        cache.write_bytes(buf.getvalue())
        doc.close()
    return Response(cache.read_bytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "max-age=86400"})


def _doc_payload(row: dict) -> dict:
    return {"id": row["id"], "filename": row["filename"], "sha256": row["sha256"],
            "pages": json.loads(row["pages_json"] or "[]"),
            "created_at": row["created_at"]}


# ------------------------------------------------------------------ runs ---

class RunRequest(BaseModel):
    document_id: int
    model: str = "byo"                    # byo | gemini/<m> | openai/<m> | ...
    schema_spec: Optional[dict] = None    # field specs; None → invoice preset
    preset: Optional[str] = None          # preset name (overrides schema_spec)
    prompt: Optional[str] = None          # extra model instructions
    extraction: Optional[dict] = None     # BYO: the JSON to ground


@app.post("/api/runs")
def create_run(req: RunRequest):
    doc = db.query_one("SELECT id FROM documents WHERE id=?", (req.document_id,))
    if doc is None:
        raise HTTPException(404, "no such document")
    if req.model in ("byo", "none") and not req.extraction:
        raise HTTPException(422, "BYO run needs `extraction` (the JSON to ground)")
    schema = req.schema_spec
    if req.preset:
        preset = db.query_one("SELECT * FROM presets WHERE name=?", (req.preset,))
        if preset is None:
            raise HTTPException(404, f"no preset named {req.preset!r}")
        schema = json.loads(preset["schema_json"] or "null")
        req.prompt = req.prompt or preset["prompt_text"]
    run_id = db.execute(
        "INSERT INTO runs(document_id,model,prompt_text,extraction_json,schema_json,"
        "backend,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (req.document_id, req.model, req.prompt,
         json.dumps(req.extraction, ensure_ascii=False) if req.extraction else None,
         json.dumps(schema, ensure_ascii=False) if schema else None,
         "auto", "queued", db.now()))
    runner.submit_run(run_id)
    return {"run_id": run_id, "status": "queued"}


def _native_boxes(run_id: int) -> dict:
    """The model's own box claims for a run, keyed by field name.
    "_error" marks a failed native pass."""
    native: dict = {}
    for row in db.query(
            "SELECT field_name, page, value, bbox_json FROM native_boxes WHERE run_id=?",
            (run_id,)):
        if row["field_name"] == "_error":
            native["_error"] = row["value"]
            continue
        native[row["field_name"]] = {
            "page": row["page"],
            "value": json.loads(row["value"]) if row["value"] else None,
            **(json.loads(row["bbox_json"]) if row["bbox_json"] else {}),
        }
    return native


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    row = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if row is None:
        raise HTTPException(404, "no such run")
    usage = json.loads(row["token_usage_json"] or "null")
    cost, cost_approx = (None, False)
    if usage:
        from .pricing import estimate_usd
        cost, cost_approx = estimate_usd(row["model"], usage.get("prompt_tokens", 0),
                                         usage.get("output_tokens", 0))
    payload = {"id": row["id"], "document_id": row["document_id"],
               "model": row["model"], "status": row["status"],
               "error": row["error"], "latency_ms": row["latency_ms"],
               "token_usage": usage,
               "timings": json.loads(row["timings_json"] or "null"),
               "progress": json.loads(row["progress_json"] or "null"),
               "cost_usd": cost, "cost_approx": cost_approx,
               "created_at": row["created_at"]}
    if row["status"] == "done" and row["result_json"]:
        payload["result"] = json.loads(row["result_json"])
    payload["native"] = _native_boxes(run_id)
    return payload


@app.get("/api/runs")
def list_runs(document_id: Optional[int] = None):
    sql = "SELECT id,document_id,arena_id,model,status,error,latency_ms,created_at FROM runs"
    params: tuple = ()
    if document_id is not None:
        sql += " WHERE document_id=?"
        params = (document_id,)
    return db.query(sql + " ORDER BY created_at DESC", params)


# ----------------------------------------------------------------- arena ---

class ArenaRequest(BaseModel):
    document_id: int
    models: list[str]
    schema_spec: Optional[dict] = None
    prompt: Optional[str] = None


@app.post("/api/arena")
def create_arena(req: ArenaRequest):
    if len(req.models) < 1:
        raise HTTPException(422, "pick at least one model")
    doc = db.query_one("SELECT id FROM documents WHERE id=?", (req.document_id,))
    if doc is None:
        raise HTTPException(404, "no such document")
    arena_id = db.execute(
        "INSERT INTO arenas(document_id, models_json, status, created_at) VALUES(?,?,?,?)",
        (req.document_id, json.dumps(req.models), "queued", db.now()))
    for model in req.models:
        db.execute(
            "INSERT INTO runs(document_id,arena_id,model,prompt_text,schema_json,"
            "backend,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (req.document_id, arena_id, model, req.prompt,
             json.dumps(req.schema_spec, ensure_ascii=False) if req.schema_spec else None,
             "auto", "queued", db.now()))
    runner.submit_arena(arena_id)
    return {"arena_id": arena_id, "status": "queued"}


def _iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / max(1e-12, ua)


def _arena_payload(arena_id: int) -> dict:
    arena = db.query_one("SELECT * FROM arenas WHERE id=?", (arena_id,))
    if arena is None:
        raise HTTPException(404, "no such arena")
    runs = db.query("SELECT * FROM runs WHERE arena_id=? ORDER BY id", (arena_id,))
    entries = []
    for run in runs:
        native = _native_boxes(run["id"])
        result = json.loads(run["result_json"]) if run["result_json"] else None

        score = None
        if result:
            fields = result["fields"]
            statuses = result["summary"]
            from paperpin.align.canon import canon_value
            ious = []
            native_agree = 0
            native_total = 0
            set_agree = 0
            for name, fr in fields.items():
                nb = native.get(name)
                if nb and nb.get("xyxy") and fr.get("bbox"):
                    native_total += 1
                    iou = _iou(fr["bbox"], nb["xyxy"])
                    ious.append(iou)
                    if iou >= 0.5:
                        native_agree += 1
                    # honest agreement: the value prints more than once and
                    # native sits on another legitimate copy — count native
                    # matching ANY of our reported locations, or our pinned
                    # evidence literally being the value (pixel-proven twin)
                    if iou >= 0.05 or any(
                            _iou(c["bbox"], nb["xyxy"]) >= 0.05
                            for c in (fr.get("candidates") or []) if c.get("bbox")):
                        set_agree += 1
                    elif fr.get("evidence") and fr.get("value") is not None:
                        v = canon_value(str(fr["value"]))
                        if v and v in canon_value(fr["evidence"]):
                            set_agree += 1
            usage = json.loads(run["token_usage_json"] or "null")
            timings = json.loads(run["timings_json"] or "null")
            cost, cost_approx = (None, False)
            if usage:
                from .pricing import estimate_usd
                cost, cost_approx = estimate_usd(
                    run["model"], usage.get("prompt_tokens", 0),
                    usage.get("output_tokens", 0))
            score = {
                "statuses": statuses,
                "located": statuses.get("verified", 0) + statuses.get("low_confidence", 0),
                "n_fields": len(fields),
                "native_boxes": len([n for n in native.values()
                                     if isinstance(n, dict) and n.get("xyxy")]),
                "mean_iou_vs_native": round(sum(ious) / len(ious), 3) if ious else None,
                "native_iou50_rate": round(native_agree / native_total, 3) if native_total else None,
                "native_agree_rate": round(set_agree / native_total, 3) if native_total else None,
                "latency_ms": run["latency_ms"],
                "timings": timings,
                "token_usage": usage,
                "cost_usd": cost,
                "cost_approx": cost_approx,
            }
        entries.append({"run_id": run["id"], "model": run["model"],
                        "status": run["status"], "error": run["error"],
                        "progress": json.loads(run["progress_json"] or "null"),
                        "result": result, "native": native, "score": score})

    # cross-model value agreement (canonical compare)
    agreement: dict[str, dict] = {}
    done = [e for e in entries if e["result"]]
    if len(done) >= 2:
        from paperpin.align.canon import canon_value
        names = set()
        for e in done:
            names |= set(e["result"]["fields"])
        for name in sorted(names):
            values = {}
            for e in done:
                fr = e["result"]["fields"].get(name)
                values[e["model"]] = fr["value"] if fr else None
            canon = {m: canon_value(str(v)) if v is not None else None
                     for m, v in values.items()}
            distinct = {c for c in canon.values() if c is not None}
            agreement[name] = {"values": values,
                               "all_agree": len(distinct) <= 1}
    return {"id": arena["id"], "document_id": arena["document_id"],
            "status": arena["status"], "error": arena["error"],
            "models": json.loads(arena["models_json"]),
            "created_at": arena["created_at"], "entries": entries,
            "agreement": agreement}


@app.get("/api/arenas")
def list_arenas(document_id: Optional[int] = None):
    sql = "SELECT id, document_id, models_json, status, created_at FROM arenas"
    params: tuple = ()
    if document_id is not None:
        sql += " WHERE document_id=?"
        params = (document_id,)
    rows = db.query(sql + " ORDER BY created_at DESC", params)
    return [{**r, "models": json.loads(r.pop("models_json"))} for r in rows]


@app.get("/api/arena/{arena_id}")
def get_arena(arena_id: int):
    return _arena_payload(arena_id)


@app.get("/api/arena/{arena_id}/export")
def export_arena(arena_id: int):
    payload = _arena_payload(arena_id)
    return Response(
        json.dumps(payload, indent=1, ensure_ascii=False).encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="paperpin_arena_{arena_id}.json"'})


@app.get("/api/runs/{run_id}/diagnostic")
def run_diagnostic(run_id: int):
    """Blame-splitting quality report. The model's values are taken as ideal;
    every field is then classified along the pipeline:

      read   — did OCR / the text layer actually contain the value's text?
               (exact canonical containment / partial ≥70% / missing)
      pinned — did paperpin locate it (verified/low_confidence)?

      verdict: clean         read exact  + pinned      (all good)
               rescued       read partial+ pinned      (damaged text, still pinned)
               aligner_miss  read exact  + NOT pinned  (OUR bug — fixable)
               matcher_gap   read partial+ NOT pinned  (needs a smarter fallback)
               ocr_miss      read missing               (OCR frontier — not today's fight)
    """
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if run is None or not run["result_json"]:
        raise HTTPException(404, "no finished run to diagnose")
    doc_row = db.query_one("SELECT * FROM documents WHERE id=?", (run["document_id"],))
    fields = json.loads(run["result_json"])["fields"]

    from difflib import SequenceMatcher

    from paperpin.align.canon import canon_value
    from paperpin.align.rows import build_rows
    from paperpin.backends.base import get_backend
    from paperpin.geometry.segmentize import segmentize
    from paperpin.intake.loader import load_document

    doc = load_document(doc_row["bytes_path"])
    try:
        backend = get_backend("auto") if any(p.route == "ocr" for p in doc.pages) else None
        segments = []
        for page in doc.pages:
            segments.extend(segmentize(page, backend, doc.sha256).segments)
    finally:
        doc.close()
    rows = build_rows(segments)
    page_canons: dict[int, str] = {}
    page_raws: dict[int, str] = {}
    for r in rows:
        page_canons[r.page] = page_canons.get(r.page, "") + r.canon
        page_raws[r.page] = page_raws.get(r.page, "") + " " + r.text

    out = []
    counts = {"clean": 0, "rescued": 0, "aligner_miss": 0, "matcher_gap": 0,
              "ocr_miss": 0, "null": 0, "ambiguous": 0}
    for name, fr in fields.items():
        value = fr.get("value")
        if value is None:
            counts["null"] += 1
            out.append({"name": name, "value": None, "verdict": "null",
                        "read": None, "status": fr["status"]})
            continue
        vcanon = canon_value(str(value))
        if vcanon:
            exact = any(vcanon in pc for pc in page_canons.values())
            ratio = 0.0
            if not exact:
                # multi-line/reordered values (addresses): token-level presence
                tokens = [canon_value(t) for t in str(value).split()]
                tokens = [t for t in tokens if t]
                if tokens:
                    hit = max(sum(1 for t in tokens if t in pc) / len(tokens)
                              for pc in page_canons.values())
                    ratio = max(ratio, hit)
                for pc in page_canons.values():
                    m = SequenceMatcher(None, pc, vcanon).find_longest_match(
                        0, len(pc), 0, len(vcanon))
                    ratio = max(ratio, m.size / max(1, len(vcanon)))
            read = "exact" if (exact or ratio >= 0.9) else (
                "partial" if ratio >= 0.7 else "missing")
            read_ratio = 1.0 if exact else round(ratio, 2)
        else:  # symbol-only values: raw containment
            exact = any(str(value).strip() in praw for praw in page_raws.values())
            read = "exact" if exact else "missing"
            read_ratio = 1.0 if exact else 0.0

        status = fr["status"]
        pinned = status in ("verified", "low_confidence")
        if status == "ambiguous":
            counts["ambiguous"] += 1
            verdict = "ambiguous"
        elif pinned and read == "exact":
            verdict = "clean"
        elif pinned:
            verdict = "rescued"
        elif read == "exact":
            verdict = "aligner_miss"
        elif read == "partial":
            verdict = "matcher_gap"
        else:
            verdict = "ocr_miss"
        if verdict in counts:
            counts[verdict] += 1
        out.append({"name": name, "value": value, "verdict": verdict,
                    "read": read, "read_ratio": read_ratio, "status": status})

    readable = counts["clean"] + counts["rescued"] + counts["aligner_miss"] + counts["matcher_gap"]
    pinned_readable = counts["clean"] + counts["rescued"]
    total_valued = sum(v for k, v in counts.items() if k != "null")
    return {
        "run_id": run_id,
        "assumption": "model values treated as ground truth",
        "summary": {
            **counts,
            "total_fields": len(fields),
            "with_value": total_valued,
            "ocr_read_rate": round(readable / total_valued, 3) if total_valued else None,
            "aligner_recall_on_readable": round(pinned_readable / readable, 3) if readable else None,
        },
        "fields": out,
    }


@app.post("/api/runs/{run_id}/repin")
def repin_run(run_id: int, fresh: bool = False):
    """Pure-paperpin speed run: take the values a finished run extracted and
    ground them again — no model call, no tokens. `fresh=true` bypasses the
    OCR cache so the FULL pipeline (including OCR) runs and can be watched
    live; default reuses cached geometry."""
    source = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if source is None or not source["result_json"]:
        raise HTTPException(404, "no finished run to re-pin")
    fields = json.loads(source["result_json"])["fields"]

    extraction: dict = {}
    tables: dict[str, dict[int, dict]] = {}
    for name, fr in fields.items():
        if "[" in name and "]." in name:  # line_items[2].qty → rebuild the array
            tname, rest = name.split("[", 1)
            idx_s, col = rest.split("].", 1)
            tables.setdefault(tname, {}).setdefault(int(idx_s), {})[col] = fr["value"]
        else:
            entry: dict = {"value": fr["value"]}
            if fr.get("quote"):
                entry["quote"] = fr["quote"]
            extraction[name] = entry
    for tname, by_idx in tables.items():
        extraction[tname] = [by_idx[i] for i in sorted(by_idx)]

    new_id = db.execute(
        "INSERT INTO runs(document_id,model,prompt_text,extraction_json,schema_json,"
        "options_json,backend,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (source["document_id"], "byo", f"re-pin of run #{run_id}",
         json.dumps(extraction, ensure_ascii=False), source["schema_json"],
         json.dumps({"use_cache": not fresh}),
         "auto", "queued", db.now()))
    runner.submit_run(new_id)
    return {"run_id": new_id, "status": "queued", "source_run": run_id}


@app.get("/api/runs/{run_id}/export")
def export_run(run_id: int):
    row = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if row is None or not row["result_json"]:
        raise HTTPException(404, "no result for this run")
    return Response(
        row["result_json"].encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="paperpin_run_{run_id}.json"'})


# --------------------------------------------------------------- presets ---

class PresetRequest(BaseModel):
    name: str
    schema_spec: Optional[dict] = None
    prompt_text: Optional[str] = None


@app.get("/api/presets")
def list_presets():
    from paperpin.schemas import PRESETS
    builtin = [{"name": name, "schema_spec": spec, "builtin": True}
               for name, spec in PRESETS.items()]
    saved = [{"name": r["name"], "schema_spec": json.loads(r["schema_json"] or "null"),
              "prompt_text": r["prompt_text"], "builtin": False}
             for r in db.query("SELECT * FROM presets ORDER BY name")]
    return builtin + saved


@app.post("/api/presets")
def save_preset(req: PresetRequest):
    db.execute("INSERT INTO presets(name,schema_json,prompt_text) VALUES(?,?,?) "
               "ON CONFLICT(name) DO UPDATE SET schema_json=excluded.schema_json, "
               "prompt_text=excluded.prompt_text",
               (req.name, json.dumps(req.schema_spec, ensure_ascii=False)
                if req.schema_spec else None, req.prompt_text))
    return {"ok": True}


# -------------------------------------------------------- settings/models ---

class SettingsRequest(BaseModel):
    gemini_api_key: Optional[str] = None


@app.get("/api/settings")
def get_settings():
    import os
    env_key = os.environ.get("GEMINI_API_KEY")
    stored = db.get_setting("gemini_api_key")
    active = env_key or stored
    return {"gemini_key_set": bool(active),
            "gemini_key_masked": db.mask_secret(active),
            "gemini_key_source": "env" if env_key else ("lab" if stored else None)}


@app.post("/api/settings")
def set_settings(req: SettingsRequest):
    if req.gemini_api_key is not None:
        db.set_setting("gemini_api_key", req.gemini_api_key or None)
    return get_settings()


@app.get("/api/models")
def list_models():
    """Model picker data. Gemini list is fetched live when a key exists —
    names change too often to hardcode (E-38)."""
    import os
    out = [{"id": "byo", "label": "BYO-JSON (no model, offline)", "cloud": False,
            "note": "ground an existing extraction — nothing leaves this machine"}]
    key = os.environ.get("GEMINI_API_KEY") or db.get_setting("gemini_api_key")
    if key:
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://generativelanguage.googleapis.com/v1beta/models?pageSize=50",
                headers={"x-goog-api-key": key})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            for m in data.get("models", []):
                name = m["name"].split("/", 1)[-1]
                if ("generateContent" in m.get("supportedGenerationMethods", [])
                        and name.startswith("gemini")
                        and not any(t in name for t in ("tts", "image", "robotics",
                                                        "computer-use", "omni"))):
                    out.append({"id": f"gemini/{name}", "label": name, "cloud": True,
                                "note": "sends the document to Google"})
        except Exception as e:
            out.append({"id": "gemini/gemini-flash-latest",
                        "label": "gemini-flash-latest (list unavailable)",
                        "cloud": True, "note": f"model list fetch failed: {e}"})
    out.append({"id": "openai/<model>", "label": "any OpenAI-compatible endpoint",
                "cloud": True, "template": True,
                "note": "OPENAI_BASE_URL + OPENAI_API_KEY from environment"})
    return out


_DIST = Path(__file__).parent.parent / "web" / "dist"

if (_DIST / "index.html").exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="web")
else:
    @app.get("/", response_class=HTMLResponse)
    def index():
        return """<!DOCTYPE html><html><head><title>paperpin lab</title>
<style>body{background:#0b1020;color:#e8ecf8;font:15px 'Segoe UI',sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
div{text-align:center}code{background:#111831;padding:2px 8px;border-radius:6px}</style>
</head><body><div><h1>📌 paperpin lab</h1>
<p>UI not built yet — run <code>npm run build</code> in lab/web.
API docs: <a style="color:#60a5fa" href="/api/docs">/api/docs</a></p></div></body></html>"""
