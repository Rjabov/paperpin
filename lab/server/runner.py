"""Run executor — one worker thread, serial queue (E-41): OCR happens off the
request thread so the UI stays live; SQLite writes go through db's lock.
"""
from __future__ import annotations

import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import db

_EXECUTOR: Optional[ThreadPoolExecutor] = None
_EXECUTOR_LOCK = threading.Lock()


def executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=1,
                                           thread_name_prefix="paperpin-run")
        return _EXECUTOR


def reset_for_tests() -> None:
    """Drain and drop the worker pool.

    A run keeps touching the database after its status says `done` (the
    native-boxes pass). Tests that tear down while that is in flight leave a
    thread writing to a temp directory that is about to vanish, so stop
    producing work before the connection is closed.
    """
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        pool, _EXECUTOR = _EXECUTOR, None
    if pool is not None:
        pool.shutdown(wait=True)


def submit_run(run_id: int) -> None:
    executor().submit(_run_task, run_id)


def _run_task(run_id: int) -> None:
    """A single run, then the model's own-boxes pass for cloud models — the
    Lab's whole point is showing the model's claim next to the verified pin."""
    _execute_run(run_id)
    row = db.query_one(
        "SELECT status, model, document_id FROM runs WHERE id=?", (run_id,))
    if row and row["status"] == "done" and row["model"].startswith("gemini"):
        try:
            _native_pass(run_id, row["model"], row["document_id"])
        except Exception as e:
            db.execute(
                "INSERT INTO native_boxes(run_id, field_name, page, value, bbox_json) "
                "VALUES(?,?,?,?,?)",
                (run_id, "_error", None, str(e), None))


def submit_arena(arena_id: int) -> None:
    executor().submit(_execute_arena, arena_id)


def _prewarm_geometry(document_id: int) -> None:
    """Run OCR/text-layer segmentation ONCE before parallel model runs — every
    grounding afterwards is a cache hit instead of duplicated OCR."""
    from paperpin.backends.base import get_backend
    from paperpin.geometry.segmentize import segmentize
    from paperpin.intake.loader import load_document

    doc_row = db.query_one("SELECT * FROM documents WHERE id=?", (document_id,))
    if doc_row is None:
        return
    doc = load_document(doc_row["bytes_path"])
    try:
        backend = get_backend("auto") if any(p.route == "ocr" for p in doc.pages) else None
        for page in doc.pages:
            segmentize(page, backend, doc.sha256)
    finally:
        doc.close()


def _execute_arena(arena_id: int) -> None:
    """One arena = for each model: extraction run (grounded by paperpin) +
    a native-box pass asking the model for ITS OWN coordinates. Models run in
    PARALLEL — the calls are network-bound; geometry is pre-warmed once.
    Everything persists: runs, raw responses, native boxes."""
    from concurrent.futures import ThreadPoolExecutor as ArenaPool
    from concurrent.futures import as_completed

    arena = db.query_one("SELECT * FROM arenas WHERE id=?", (arena_id,))
    if arena is None:
        return
    db.execute("UPDATE arenas SET status='running' WHERE id=?", (arena_id,))
    try:
        _prewarm_geometry(arena["document_id"])
        run_rows = db.query(
            "SELECT id, model FROM runs WHERE arena_id=? ORDER BY id", (arena_id,))
        with ArenaPool(max_workers=min(4, max(1, len(run_rows)))) as pool:
            futures = [pool.submit(_arena_model_task, row, arena["document_id"])
                       for row in run_rows]
            for f in as_completed(futures):
                f.result()  # surface exceptions
        db.execute("UPDATE arenas SET status='done', finished_at=? WHERE id=?",
                   (db.now(), arena_id))
    except Exception as e:
        db.execute("UPDATE arenas SET status='error', error=?, finished_at=? WHERE id=?",
                   (f"{type(e).__name__}: {e}", db.now(), arena_id))
        traceback.print_exc()


def _arena_model_task(row: dict, document_id: int) -> None:
    _execute_run(row["id"])
    run = db.query_one("SELECT status FROM runs WHERE id=?", (row["id"],))
    if run and run["status"] == "done" and row["model"].startswith("gemini"):
        try:
            _native_pass(row["id"], row["model"], document_id)
        except Exception as e:
            db.execute(
                "INSERT INTO native_boxes(run_id, field_name, page, value, bbox_json) "
                "VALUES(?,?,?,?,?)",
                (row["id"], "_error", None, str(e), None))


def _native_pass(run_id: int, model: str, document_id: int) -> None:
    import time as _time

    from paperpin.adapters.gemini import GeminiAdapter
    from paperpin.intake.loader import load_document

    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    doc_row = db.query_one("SELECT * FROM documents WHERE id=?", (document_id,))
    # box the fields the extraction actually produced (includes line_items[i].col)
    result = json.loads(run["result_json"] or "{}")
    field_names = [n for n, f in result.get("fields", {}).items()
                   if f.get("value") is not None][:48]
    if not field_names:
        return
    specs = {name: None for name in field_names}

    t0 = _time.perf_counter()
    doc = load_document(doc_row["bytes_path"])
    try:
        images = [p.raster() for p in doc.pages]
        adapter = GeminiAdapter(model.split("/", 1)[-1])
        boxes, meta = adapter.native_boxes(specs, images)
    finally:
        doc.close()
    native_s = round(_time.perf_counter() - t0, 2)

    db.execute("INSERT INTO model_responses(run_id, raw_json, parsed_ok) VALUES(?,?,1)",
               (run_id, meta.get("_raw_response")))
    usage_prev = json.loads(run["token_usage_json"] or "{}")
    usage_native = meta.get("token_usage", {})
    usage_prev["native"] = usage_native
    usage_prev["prompt_tokens"] = usage_prev.get("prompt_tokens", 0) + usage_native.get("prompt_tokens", 0)
    usage_prev["output_tokens"] = usage_prev.get("output_tokens", 0) + usage_native.get("output_tokens", 0)
    timings = json.loads(run["timings_json"] or "{}")
    timings["native_s"] = native_s
    db.execute("UPDATE runs SET token_usage_json=?, timings_json=? WHERE id=?",
               (json.dumps(usage_prev), json.dumps(timings), run_id))

    for name, entry in boxes.items():
        if not isinstance(entry, dict):
            continue
        raw_box = entry.get("box_2d")
        xyxy = None
        if (isinstance(raw_box, list) and len(raw_box) == 4
                and all(isinstance(v, (int, float)) for v in raw_box)):
            ymin, xmin, ymax, xmax = raw_box
            xyxy = [xmin / 1000, ymin / 1000, xmax / 1000, ymax / 1000]
        db.execute(
            "INSERT INTO native_boxes(run_id, field_name, page, value, bbox_json) "
            "VALUES(?,?,?,?,?)",
            (run_id, name, entry.get("page"),
             json.dumps(entry.get("value"), ensure_ascii=False),
             json.dumps({"raw": raw_box, "xyxy": xyxy})))


def _make_progress_reporter(run_id: int):
    """Appends stage events to runs.progress_json AS THEY HAPPEN — the UI
    polls and renders the pipeline live."""
    import time as _time
    events: list[dict] = []
    t0 = _time.perf_counter()

    def report(stage: str, phase: str, info: dict) -> None:
        now = round(_time.perf_counter() - t0, 3)
        if phase == "start":
            events.append({"stage": stage, "start_s": now, "info": info})
        else:
            for ev in reversed(events):
                if ev["stage"] == stage and "end_s" not in ev:
                    ev["end_s"] = now
                    ev["info"] = {**ev.get("info", {}), **info}
                    break
        db.execute("UPDATE runs SET progress_json=? WHERE id=?",
                   (json.dumps(events, ensure_ascii=False), run_id))

    return report


def _execute_run(run_id: int) -> None:
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if run is None or run["status"] not in ("queued",):
        return
    db.execute("UPDATE runs SET status='running', started_at=?, progress_json=? "
               "WHERE id=?", (db.now(), "[]", run_id))
    try:
        result = _run_pipeline(run, _make_progress_reporter(run_id))
        payload = result.to_dict()
        meta = result.meta
        usage = meta.get("token_usage") or {}
        token_json = {"prompt_tokens": usage.get("prompt_tokens", 0),
                      "output_tokens": usage.get("output_tokens", 0),
                      "extract": usage} if usage else None
        timings = {"extract_s": meta.get("extract_seconds", 0),
                   "ground_s": meta.get("ground_seconds", 0)}
        db.execute(
            "UPDATE runs SET status='done', finished_at=?, latency_ms=?, "
            "token_usage_json=?, timings_json=?, result_json=? WHERE id=?",
            (db.now(),
             int(1000 * (meta.get("extract_seconds", 0) + meta.get("ground_seconds", 0))),
             json.dumps(token_json) if token_json else None,
             json.dumps(timings),
             json.dumps(payload, ensure_ascii=False),
             run_id))
        if meta.get("_raw_response") is not None:
            db.execute("INSERT INTO model_responses(run_id, raw_json, parsed_ok) "
                       "VALUES(?,?,1)", (run_id, meta["_raw_response"]))
        for name, fr in payload["fields"].items():
            db.execute(
                "INSERT INTO fields(run_id,name,value_json,status,confidence,page,"
                "bbox_json,evidence,anchor,method,notes_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, name, json.dumps(fr["value"], ensure_ascii=False),
                 fr["status"], fr.get("confidence"), fr.get("page"),
                 json.dumps(fr.get("bbox")), fr.get("evidence"), fr.get("anchor"),
                 fr.get("method"), json.dumps(fr.get("notes", []), ensure_ascii=False)))
    except Exception as e:  # surface, never crash the worker
        db.execute("UPDATE runs SET status='error', finished_at=?, error=? WHERE id=?",
                   (db.now(), f"{type(e).__name__}: {e}", run_id))
        traceback.print_exc()


def _run_pipeline(run: dict, progress=None):
    from paperpin.api import extract, ground

    doc = db.query_one("SELECT * FROM documents WHERE id=?", (run["document_id"],))
    if doc is None:
        raise ValueError("document disappeared from the database")
    schema = json.loads(run["schema_json"]) if run["schema_json"] else None
    model = run["model"]
    options = json.loads(run["options_json"] or "{}") if "options_json" in run.keys() else {}
    use_cache = options.get("use_cache", True)

    # key precedence: environment (.env) → lab settings table
    if model.startswith("gemini") and not _env_key("GEMINI_API_KEY"):
        stored = db.get_setting("gemini_api_key")
        if stored:
            import os
            os.environ["GEMINI_API_KEY"] = stored

    if model in ("byo", "none"):
        if not run["extraction_json"]:
            raise ValueError("BYO run needs an extraction JSON")
        return ground(doc["bytes_path"],
                      extraction=json.loads(run["extraction_json"]), schema=schema,
                      use_cache=use_cache, progress=progress)
    return extract(doc["bytes_path"], schema=schema, model=model,
                   prompt=run["prompt_text"], use_cache=use_cache,
                   progress=progress)


def _env_key(name: str) -> Optional[str]:
    import os
    return os.environ.get(name)
