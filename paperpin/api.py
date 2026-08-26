"""Public API (§4.1): ground() and extract().

    from paperpin import ground, extract

    # the layer play — ground ANY existing extraction:
    result = ground("invoice.pdf", extraction={"total": "146,14", ...})

    # the full pipeline — extraction included:
    result = extract("photo.jpg", schema="invoice", model="gemini/gemini-2.5-flash")

Pipeline (§6.1): INTAKE → GEOMETRY → EXTRACTION → ALIGNMENT → VERIFICATION → OUTPUTS.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .adapters.base import get_adapter, load_byo_extraction
from .align.aligner import align_fields
from .align.rows import build_rows
from .backends.base import get_backend
from .geometry.segmentize import segmentize
from .intake.loader import Document, load_document
from .schemas import infer_spec, resolve_schema
from .types import FieldResult, FieldSpec, FieldType, GroundResult, Status


ProgressFn = Any  # Callable[(stage: str, phase: "start"|"end", info: dict), None]


_warned_progress = False


def _notify(progress: Optional[ProgressFn], stage: str, phase: str,
            info: Optional[dict] = None) -> None:
    if progress is None:
        return
    try:
        progress(stage, phase, info or {})
    except Exception as e:
        # progress reporting must never break a run — but a broken callback
        # deserves one visible warning, not eternal silence
        global _warned_progress
        if not _warned_progress:
            import warnings
            warnings.warn(f"progress callback raised {e!r}; further errors "
                          "suppressed", stacklevel=2)
            _warned_progress = True


def ground(source, extraction: Any, schema=None, backend: str = "auto",
           use_cache: bool = True, progress: Optional[ProgressFn] = None) -> GroundResult:
    """Ground an existing extraction (anyone's JSON) against the document."""
    doc = load_document(source)
    specs = resolve_schema(schema)
    payload = load_byo_extraction(extraction)
    return _run(doc, payload, specs, backend,
                adapter_meta={"adapter": "byo"}, use_cache=use_cache,
                progress=progress)


def extract(source, schema=None, model: str = "byo",
            prompt: Optional[str] = None, extraction: Any = None,
            backend: str = "auto", use_cache: bool = True,
            api_key: Optional[str] = None, base_url: Optional[str] = None,
            timeout: float = 180.0,
            progress: Optional[ProgressFn] = None) -> GroundResult:
    """Extract fields with a model (or take BYO JSON), then ground every value."""
    doc = load_document(source)
    specs = resolve_schema(schema)
    if model in ("byo", "none", ""):
        if extraction is None:
            raise ValueError("model='byo' needs extraction= (dict, JSON, or file path)")
        payload = load_byo_extraction(extraction)
        meta: dict = {"adapter": "byo"}
    else:
        adapter = get_adapter(model, api_key=api_key, base_url=base_url,
                              timeout=timeout)
        page_texts, page_images, n_pages = _adapter_inputs(doc)
        _notify(progress, "model read", "start", {"model": model})
        t0 = time.perf_counter()
        payload, meta = adapter.extract(doc, specs, prompt, page_texts, page_images)
        meta["extract_seconds"] = round(time.perf_counter() - t0, 2)
        if n_pages > max(len(page_texts), len(page_images)):
            meta["pages_truncated"] = n_pages - max(len(page_texts),
                                                    len(page_images))
        _notify(progress, "model read", "end", {"model": model})
    return _run(doc, payload, specs, backend, adapter_meta=meta,
                use_cache=use_cache, progress=progress)


def _infer_table_spec(name: str, row_objects: list) -> FieldSpec:
    """BYO lists of row objects become table specs with inferred column types."""
    columns: dict[str, FieldSpec] = {}
    for obj in row_objects:
        if isinstance(obj, dict):
            for col, val in obj.items():
                if col not in columns and val is not None:
                    columns[col] = infer_spec(col, val)
    return FieldSpec(name=name, type=FieldType.TABLE, columns=columns or None)


ADAPTER_MAX_PAGES = 12  # pages actually sent to a model


def _adapter_inputs(doc: Document):
    """Text pages ship as text (cheap); any OCR-routed page switches the whole
    call to images so the model sees what the aligner will see. Only the
    pages that will be SENT are rasterized — rendering a 60-page PDF to feed
    a 12-page call held every raster in memory for nothing."""
    if all(p.route == "textlayer" for p in doc.pages):
        texts = []
        for p in doc.pages:
            texts.append(p.pdf_page.extract_text() or "")
        return texts, [], len(doc.pages)
    images = [p.raster() for p in doc.pages[:ADAPTER_MAX_PAGES]]
    return [], images, len(doc.pages)


def _run(doc: Document, extraction: dict, specs: dict[str, FieldSpec],
         backend_name: str, adapter_meta: Optional[dict],
         use_cache: bool, progress: Optional[ProgressFn] = None) -> GroundResult:
    from .profile import StageTimer
    extraction = dict(extraction)  # never mutate the caller's dict
    timer = StageTimer()
    t0 = time.perf_counter()
    _notify(progress, "intake", "start")

    # fill in specs for fields the schema doesn't cover (BYO-JSON, E-40)
    for name, value in extraction.items():
        if name not in specs:
            raw = value.get("value") if isinstance(value, dict) and "value" in value else value
            specs[name] = infer_spec(name, raw)

    # split off table fields (line items) — they are grounded row-by-row.
    # A list of SCALARS is not a table: each element grounds as its own
    # indexed field, so every asserted value still gets an honest status.
    tables: dict[str, list] = {}
    for name in list(extraction):
        spec = specs.get(name)
        value = extraction[name]
        if (spec and spec.type == FieldType.TABLE) or isinstance(value, list):
            items = extraction.pop(name) or []
            if items and not any(isinstance(x, dict) for x in items)                     and not (spec and spec.type == FieldType.TABLE):
                for idx, x in enumerate(items):
                    flat = f"{name}[{idx}]"
                    extraction[flat] = x
                    specs[flat] = infer_spec(flat, x)
                continue
            tables[name] = items
            if not (spec and spec.type == FieldType.TABLE):
                specs[name] = _infer_table_spec(name, tables[name])

    # resolve the backend even when nothing needs OCR: a wrong or removed name
    # used to pass silently on a text-layer document and only surface on the
    # caller's first scan, long after the typo. Constructing one is free —
    # the OCR engine itself loads lazily on first use.
    backend = get_backend(backend_name)
    needs_ocr = any(p.route == "ocr" for p in doc.pages)
    ocr_backend = backend if needs_ocr else None
    timer.stage("intake_s")
    _notify(progress, "intake", "end", {"pages": len(doc.pages)})

    segments = []
    route_by_page: dict[int, str] = {}
    orientation_by_page: dict[int, int] = {}
    geometry_profile: list[dict] = []
    for page in doc.pages:
        stage_name = f"page {page.index + 1} · {'text-layer' if page.route == 'textlayer' else 'ocr'}"
        _notify(progress, stage_name, "start")
        pt0 = time.perf_counter()
        ps = segmentize(page, ocr_backend, doc.sha256, use_cache=use_cache)
        segments.extend(ps.segments)
        route_by_page[page.index] = ps.route
        orientation_by_page[page.index] = ps.orientation_k
        cache_hit = bool((ps.meta or {}).get("cache_hit"))
        geometry_profile.append({
            "page": page.index, "route": ps.route,
            "cache_hit": cache_hit,
            "segments": len(ps.segments),
            "s": round(time.perf_counter() - pt0, 4),
        })
        _notify(progress, stage_name, "end",
                {"segments": len(ps.segments), "cache_hit": cache_hit})
    timer.stage("geometry_s")

    _notify(progress, "align", "start")
    page_sizes = {p.index: p.size for p in doc.pages}
    rows = build_rows(segments, orientations=orientation_by_page,
                      page_sizes=page_sizes)
    timer.stage("rows_s")
    results = align_fields(rows, page_sizes, extraction, specs)

    from .align.tables import align_table
    for name, row_objects in tables.items():
        if not row_objects:
            # "no line items" is an assertion too — an empty list used to
            # vanish from the result entirely, breaking the every-field-
            # gets-a-status promise
            results[name] = FieldResult(
                name=name, value=[], status=Status.NOT_PRESENT, confidence=1.0,
                notes=["model returned an empty list — no rows asserted"])
            continue
        tresults = align_table(name, specs[name], row_objects, rows, page_sizes)
        results.update(tresults)
        for flat in tresults:  # per-cell specs so verification knows the types
            col = flat.rsplit(".", 1)[-1]
            cspec = (specs[name].columns or {}).get(col)
            if cspec:
                from dataclasses import replace as _replace
                # proof operands are row-scoped names; on a flattened spec
                # they would resolve against top-level fields (wrong scope) —
                # the row relation already ran in align_table against the row
                specs[flat] = _replace(cspec, name=flat, columns=None, proof=None)
    if tables:
        # table cells merged AFTER align_fields ran its resolver — without a
        # second pass a shared cell span carries no note at all (F6)
        from .align.aligner import _resolve_shared_instances
        _resolve_shared_instances(results)
    timer.stage("align_s")
    _notify(progress, "align", "end", {"rows": len(rows), "fields": len(results)})

    _notify(progress, "verify", "start")
    if ocr_backend is not None:
        # rescue first: a recovered value must face the same proofs
        # (checksum, plausibility) as everything else
        from .verify.rescue import rescue_not_founds
        rescue_not_founds(results, specs, rows, page_sizes, route_by_page,
                          lambda idx: doc.pages[idx].raster(), ocr_backend)
    from .verify import verify_results
    verify_results(results, specs, rows, route_by_page,
                   page_image_provider=lambda idx: doc.pages[idx].raster(),
                   ocr_backend=ocr_backend)
    timer.stage("verify_s")
    _notify(progress, "verify", "end")

    meta = dict(adapter_meta or {})
    if doc.pages_dropped:
        # a field on an undecoded page would otherwise wear the
        # hallucination flag with no hint the page was never looked at
        meta["pages_truncated"] = doc.pages_dropped
    meta["ground_seconds"] = round(time.perf_counter() - t0, 2)
    meta["backend"] = ocr_backend.name if ocr_backend else "textlayer"
    # honesty stats (§DEEP-2): when OCR reads little of a page (handwriting,
    # damage), pins are few for a REASON — integrators need the number, not a
    # guess. Raw facts only, no classification.
    ocr_page_idxs = sorted(i for i, r in route_by_page.items() if r == "ocr")
    if ocr_page_idxs:
        per_page = []
        for i in ocr_page_idxs:
            confs = [s.conf for s in segments if s.page == i]
            per_page.append({
                "page": i, "segments": len(confs),
                "mean_conf": round(sum(confs) / len(confs), 3) if confs else 0.0})
        meta["ocr"] = {"pages": per_page}
    profile = timer.finish()
    profile["geometry"] = geometry_profile
    profile["n_segments"] = len(segments)
    profile["n_rows"] = len(rows)
    profile["n_fields"] = len(results)
    if adapter_meta and adapter_meta.get("extract_seconds"):
        profile["model_read_s"] = adapter_meta["extract_seconds"]
    meta["profile"] = profile
    meta["_document"] = doc  # keeps the PDF handle alive for lazy rendering
    meta["_page_image_provider"] = lambda idx: doc.pages[idx].raster()

    result = GroundResult(fields=results, pages=[p.info() for p in doc.pages],
                          source=doc.source, meta=meta)
    return result
