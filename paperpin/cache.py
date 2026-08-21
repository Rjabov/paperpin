"""OCR segment cache (HANDOVER §6.7): re-running a document with a new prompt
must NOT re-OCR. Keyed by document sha256 + page + backend + parameters.
Stored as JSON under ~/.paperpin/cache/segments/. Zero telemetry — this never
leaves the machine.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .types import Segment


def cache_dir() -> Path:
    root = os.environ.get("PAPERPIN_HOME", "")
    base = Path(root) if root else Path.home() / ".paperpin"
    d = base / "cache" / "segments"
    d.mkdir(parents=True, exist_ok=True)
    private_dir(base)
    return d


def private_dir(path: Path) -> Path:
    """Own-user-only (0700). Cached OCR holds the document's full text —
    on a shared machine the default 0755 hands it to every local account."""
    try:
        path.chmod(0o700)
    except OSError:
        pass  # best-effort: exotic filesystems (FAT, some network mounts)
    return path


def write_private(path: Path, text: str) -> None:
    """Write 0600 from the start — never world-readable, not even briefly."""
    import os as _os
    fd = _os.open(path, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
    with _os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def _key_path(doc_sha: str, page_index: int, backend: str, variant: str) -> Path:
    # backend names come from a public Protocol — never let one write
    # outside the cache directory
    return cache_dir() / f"{doc_sha[:32]}_p{page_index}_{_safe(variant)}.json"


def load_segments(doc_sha: str, page_index: int, backend: str, variant: str
                  ) -> Optional[tuple[list[Segment], dict]]:
    try:
        p = _key_path(doc_sha, page_index, backend, variant)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        meta = data.get("meta", {})
        # shape-validate: a version-skewed or hand-corrupted entry whose JSON
        # parses fine used to explode later in rows.py — anything off is a miss
        if not isinstance(meta, dict) or not isinstance(data.get("segments"), list):
            return None
        segs = []
        for s in data["segments"]:
            seg = Segment(**{**s, "quad": [tuple(q) for q in s["quad"]] if s.get("quad") else None,
                             "char_boxes": [tuple(c) for c in s["char_boxes"]] if s.get("char_boxes") else None})
            if (not isinstance(seg.text, str)
                    or not all(isinstance(v, (int, float))
                               for v in (seg.x0, seg.top, seg.x1, seg.bottom))):
                return None
            segs.append(seg)
        return segs, meta
    except Exception:
        return None  # corrupt cache entries are ignored, never fatal


def save_segments(doc_sha: str, page_index: int, backend: str, variant: str,
                  segments: list[Segment], meta: dict) -> None:
    try:
        p = _key_path(doc_sha, page_index, backend, variant)
    except OSError:
        return  # unwritable cache home — best-effort means never fatal
    payload = {
        "meta": meta,
        "segments": [{
            "text": s.text, "x0": s.x0, "top": s.top, "x1": s.x1, "bottom": s.bottom,
            "conf": s.conf, "page": s.page, "quad": s.quad, "char_boxes": s.char_boxes,
        } for s in segments],
    }
    try:
        write_private(p, json.dumps(payload, ensure_ascii=False))
    except OSError:
        pass  # cache is best-effort
