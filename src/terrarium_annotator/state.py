"""Annotator database: one shared SQLite file for all v2 state.

Architecture §7: entry/revision/source, story_log/tree, transcript, and
run_state live in ONE `annotator.db`. `connect_annotator_db` returns the
shared connection every store is constructed over, so resume state and
content state share a transaction boundary.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from terrarium_annotator.glossary.store import SCHEMA as GLOSSARY_SCHEMA
from terrarium_annotator.memory.story_log import SCHEMA as STORY_SCHEMA

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcript(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pass_id TEXT NOT NULL,
    thread_id INTEGER NOT NULL,
    batch_index INTEGER NOT NULL,
    log_seq INTEGER,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_state(
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pass_id TEXT NOT NULL,
    thread_id INTEGER NOT NULL,
    batch_index INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def connect_annotator_db(path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the shared annotator database."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(STORY_SCHEMA)
    conn.executescript(GLOSSARY_SCHEMA)
    conn.executescript(STATE_SCHEMA)
    conn.commit()
    return conn


def save_run_state(
    conn: sqlite3.Connection, pass_id: str, thread_id: int, batch_index: int
) -> None:
    """Record the next unprocessed batch position (idempotent upsert)."""
    conn.execute(
        "INSERT INTO run_state VALUES (1, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET pass_id = excluded.pass_id, "
        "thread_id = excluded.thread_id, batch_index = excluded.batch_index, "
        "updated_at = excluded.updated_at",
        (
            pass_id,
            thread_id,
            batch_index,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def load_run_state(conn: sqlite3.Connection) -> tuple[int, int] | None:
    """(thread_id, batch_index) of the next unprocessed batch, if any."""
    row = conn.execute(
        "SELECT thread_id, batch_index FROM run_state WHERE id = 1"
    ).fetchone()
    return (row[0], row[1]) if row else None


def record_transcript(
    conn: sqlite3.Connection,
    *,
    pass_id: str,
    thread_id: int,
    batch_index: int,
    log_seq: int | None,
    role: str,
    content: str | None,
    tool_calls: list | None = None,
) -> None:
    """Append one message to the per-batch agent transcript (append-only)."""
    conn.execute(
        "INSERT INTO transcript(pass_id, thread_id, batch_index, log_seq,"
        " role, content, tool_calls, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pass_id,
            thread_id,
            batch_index,
            log_seq,
            role,
            content,
            json.dumps(tool_calls) if tool_calls else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
