"""Tiny .env loader (zero-dep). Values never override an already-set
environment variable, are never logged, and never appear in outputs (E-37)."""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(start: str | Path = ".") -> None:
    """Load the nearest .env walking up from `start`, stopping at the home
    directory — an ancestor .env above home (shared machines, /tmp) must
    never leak keys into a run. Silent no-op if absent."""
    d = Path(start).resolve()
    home = Path.home().resolve()
    for candidate in (d, *d.parents):
        p = candidate / ".env"
        if p.is_file():
            _apply(p)
            return
        if candidate == home:
            return


def _apply(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # a UTF-16 or Latin-1 .env (Windows shells write these) must not
        # kill every CLI command — env loading is best-effort
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
