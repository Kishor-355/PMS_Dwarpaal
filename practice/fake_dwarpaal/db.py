"""Layer 1 — the database.

Real project: Dwarpaal's MySQL, schema `dwarpal`.
Practice rig:  SQLite, one file, no server, no account.

SQLite speaks real SQL, so every query you write here transfers to MySQL
almost verbatim. That is the whole point of using it for practice.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS DP_AUTH_ENTRY (
    AUTH_ID         TEXT PRIMARY KEY,
    EXT_EVENT_ID    TEXT NOT NULL,          -- the bookingId we were sent
    BOOKING_ROOM_ID TEXT,
    ROOM_ID         TEXT,                   -- extRoomId currently granted
    ROOM_NUMBER     TEXT,                   -- extRoomNumber (a label, not a number)
    ARRIVAL_DATE    TEXT,                   -- ISO-8601 UTC
    DEPARTURE_DATE  TEXT,
    AUTH_STATUS     TEXT NOT NULL,          -- A = active, I = inactive
    CREATED_DATE    TEXT NOT NULL,
    UPDATED_DATE    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS DP_AUTH_DEVICE_CHARS (
    ID          INTEGER PRIMARY KEY AUTOINCREMENT,
    AUTH_ID     TEXT NOT NULL,
    DEVICE_ID   TEXT NOT NULL,              -- the physical lock
    DEVICE_CHAR TEXT NOT NULL,              -- the keycode placed on it
    CHAR_STATUS TEXT NOT NULL,              -- ACTIVE / REVOKED
    CREATED_DATE TEXT NOT NULL,
    UPDATED_DATE TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS DP_COMMUNICATION_LOG (
    ID          INTEGER PRIMARY KEY AUTOINCREMENT,
    AUTH_ID     TEXT NOT NULL,
    CHANNEL     TEXT NOT NULL,              -- SMS / EMAIL
    RECIPIENT   TEXT,
    STATUS      TEXT NOT NULL,              -- SENT
    EVENT_TYPE  TEXT,
    CREATED_DATE TEXT NOT NULL
);

-- Not in the real guide: a small trace table so the dashboard can show what
-- arrived, and so a run can be reconstructed. Kept deliberately separate from
-- the three DP_* tables so it never masks an assertion failure.
CREATE TABLE IF NOT EXISTS RIG_EVENTS (
    ID          INTEGER PRIMARY KEY AUTOINCREMENT,
    EVENT_TYPE  TEXT NOT NULL,
    BOOKING_ID  TEXT,
    NOTIFY      INTEGER,
    BODY        TEXT NOT NULL,
    RECEIVED_AT TEXT NOT NULL
);
"""

TABLES = ("DP_AUTH_ENTRY", "DP_AUTH_DEVICE_CHARS", "DP_COMMUNICATION_LOG", "RIG_EVENTS")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


def connect(path: str) -> sqlite3.Connection:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    return conn


def reset(conn: sqlite3.Connection) -> None:
    for table in TABLES:
        conn.execute(f"DELETE FROM {table}")


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]
