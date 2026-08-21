"""Lab database — SQLite, kept boring (HANDOVER §5.2).

One writer lock + WAL; hand migrations via PRAGMA user_version. Everything
lives under ~/.paperpin/lab/ (override with PAPERPIN_HOME). Raw API responses
are stored verbatim (model_responses) — Dima asked for that explicitly.
API keys live in the settings table, masked in every API response, never
logged, never exported.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None

SCHEMA_VERSION = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY,
  filename TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  mime TEXT,
  pages INTEGER,
  bytes_path TEXT NOT NULL,
  pages_json TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS arenas(
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  models_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued|running|done|error
  error TEXT,
  created_at REAL NOT NULL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  arena_id INTEGER REFERENCES arenas(id),
  model TEXT NOT NULL,
  prompt_text TEXT,
  extraction_json TEXT,            -- BYO runs: the caller-provided extraction
  schema_json TEXT,
  backend TEXT,
  status TEXT NOT NULL DEFAULT 'queued',   -- queued|running|done|error
  error TEXT,
  started_at REAL,
  finished_at REAL,
  latency_ms INTEGER,
  token_usage_json TEXT,
  timings_json TEXT,               -- {"extract_s":..,"ground_s":..,"native_s":..}
  progress_json TEXT,              -- live stage events during the run
  options_json TEXT,               -- {"use_cache": false} etc.
  cost_estimate REAL,
  result_json TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS model_responses(
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  raw_json TEXT,
  parsed_ok INTEGER
);
CREATE TABLE IF NOT EXISTS fields(
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  name TEXT NOT NULL,
  value_json TEXT,
  status TEXT,
  confidence REAL,
  page INTEGER,
  bbox_json TEXT,
  evidence TEXT,
  anchor TEXT,
  method TEXT,
  notes_json TEXT
);
CREATE TABLE IF NOT EXISTS native_boxes(
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  field_name TEXT NOT NULL,
  page INTEGER,
  value TEXT,
  bbox_json TEXT            -- {"raw": model-verbatim box_2d, "xyxy": normalized}
);
CREATE TABLE IF NOT EXISTS corrections(
  id INTEGER PRIMARY KEY,
  field_id INTEGER NOT NULL REFERENCES fields(id),
  action TEXT NOT NULL,            -- accept|reject|fix
  corrected_value TEXT,
  corrected_bbox_json TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS presets(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  schema_json TEXT,
  prompt_text TEXT
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_doc ON runs(document_id);
CREATE INDEX IF NOT EXISTS idx_fields_run ON fields(run_id);
"""


def lab_home() -> Path:
    from paperpin.cache import private_dir

    root = os.environ.get("PAPERPIN_HOME", "")
    base = Path(root) if root else Path.home() / ".paperpin"
    d = base / "lab"
    (d / "docs").mkdir(parents=True, exist_ok=True)
    (d / "pages").mkdir(parents=True, exist_ok=True)
    # the Lab stores uploaded documents and an API key — own-user-only
    for p in (base, d, d / "docs", d / "pages"):
        private_dir(p)
    return d


def connect() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        db_path = lab_home() / "lab.sqlite"
        # create 0600 before sqlite touches it: the settings table holds the
        # user's API key, and the -wal file inherits the db's mode
        if not db_path.exists():
            os.close(os.open(db_path, os.O_CREAT | os.O_WRONLY, 0o600))
        _CONN = sqlite3.connect(db_path, check_same_thread=False)
        _CONN.row_factory = sqlite3.Row
        _CONN.execute("PRAGMA journal_mode=WAL")
        _CONN.execute("PRAGMA foreign_keys=ON")
        _migrate(_CONN)
    return _CONN


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
        return
    if version == 1:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS arenas(
              id INTEGER PRIMARY KEY,
              document_id INTEGER NOT NULL REFERENCES documents(id),
              models_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'queued',
              error TEXT,
              created_at REAL NOT NULL,
              finished_at REAL
            );
            ALTER TABLE runs ADD COLUMN arena_id INTEGER REFERENCES arenas(id);
            ALTER TABLE native_boxes ADD COLUMN page INTEGER;
            ALTER TABLE native_boxes ADD COLUMN value TEXT;
        """)
        conn.execute("PRAGMA user_version=2")
        conn.commit()
        version = 2
    if version == 2:
        conn.execute("ALTER TABLE runs ADD COLUMN timings_json TEXT")
        conn.execute("PRAGMA user_version=3")
        conn.commit()
        version = 3
    if version == 3:
        conn.execute("ALTER TABLE runs ADD COLUMN progress_json TEXT")
        conn.execute("ALTER TABLE runs ADD COLUMN options_json TEXT")
        conn.execute("PRAGMA user_version=4")
        conn.commit()


def execute(sql: str, params: tuple = ()) -> int:
    """Locked write; returns lastrowid."""
    conn = connect()
    with _LOCK:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = connect()
    with _LOCK:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple = ()) -> Optional[dict]:
    rows = query(sql, params)
    return rows[0] if rows else None


def now() -> float:
    return time.time()


# ------------------------------------------------------------- settings ---

def get_setting(key: str) -> Optional[str]:
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else None


def set_setting(key: str, value: Optional[str]) -> None:
    if value is None:
        execute("DELETE FROM settings WHERE key=?", (key,))
    else:
        execute("INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def mask_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def reset_for_tests() -> None:
    """Close and forget the connection (tests point PAPERPIN_HOME elsewhere)."""
    global _CONN
    if _CONN is not None:
        _CONN.close()
        _CONN = None
