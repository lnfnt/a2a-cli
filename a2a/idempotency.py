"""Idempotency store for retried write commands (DESIGN-A2A-V05 §3, v0.5.0).

`--idempotency-key K` on `task new` / `mail send`: a duplicate key within a
24h window returns the ORIGINAL record id with `duplicate_suppressed` in the
envelope metadata instead of writing a second record. Storage:
`.state/idempotency.sqlite` guarded by flock (single machine, many profiles).

Lifecycle: `lookup` (is there a prior result?) -> `reserve` (claim the key,
prevent double-write races) -> `confirm` (record actually landed). A
reservation without confirmation is expired by lookup after the write window
— a crashed send does not burn the key for 24h silently; the reserve row is
kept but unconfirmed rows do not suppress (they are re-attemptable).
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import util

WINDOW_SECONDS = 24 * 3600
RESERVE_GRACE_SECONDS = 300  # unconfirmed reservations older than this are re-attemptable


def db_path() -> Path:
    return util.HOME / ".state" / "idempotency.sqlite"


def _lock_path() -> Path:
    return db_path().with_name("idempotency.lock")


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency (
            op_key TEXT PRIMARY KEY,
            id TEXT NOT NULL,
            created_at REAL NOT NULL,
            confirmed INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    return conn


def _purge(conn: sqlite3.Connection) -> None:
    cutoff = time.time() - WINDOW_SECONDS
    conn.execute("DELETE FROM idempotency WHERE created_at < ?", (cutoff,))
    conn.commit()


def _with_lock(fn):
    lock = _lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fn()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def lookup(op: str, key: str) -> str | None:
    """Return original id for a confirmed (or freshly reserved) key, else None."""
    if not key:
        return None

    def _do() -> str | None:
        conn = _connect()
        try:
            _purge(conn)
            row = conn.execute(
                "SELECT id, created_at, confirmed FROM idempotency WHERE op_key = ?",
                (f"{op}\x00{key}",),
            ).fetchone()
            if not row:
                return None
            id_, created_at, confirmed = row
            if confirmed:
                return id_
            # reserved but not yet confirmed: suppress only while the writer
            # is plausibly still running; stale reservations reopen the key.
            if time.time() - created_at <= RESERVE_GRACE_SECONDS:
                return id_
            return None
        finally:
            conn.close()

    return _with_lock(_do)


def reserve(op: str, key: str, id_: str) -> None:
    """Claim the key before the first write (INSERT OR IGNORE keeps the
    original winner under races)."""
    if not key:
        return

    def _do() -> None:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO idempotency (op_key, id, created_at, confirmed) VALUES (?,?,?,0)",
                (f"{op}\x00{key}", id_, time.time()),
            )
            conn.commit()
        finally:
            conn.close()

    _with_lock(_do)


def confirm(op: str, key: str, id_: str) -> None:
    """Mark the key's record as durably written."""
    if not key:
        return

    def _do() -> None:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE idempotency SET confirmed = 1 WHERE op_key = ?",
                (f"{op}\x00{key}",),
            )
            conn.commit()
        finally:
            conn.close()

    _with_lock(_do)


def stats() -> dict[str, Any]:
    def _do() -> dict[str, Any]:
        conn = _connect()
        try:
            n = conn.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0]
            unconfirmed = conn.execute(
                "SELECT COUNT(*) FROM idempotency WHERE confirmed = 0"
            ).fetchone()[0]
            return {"rows": n, "unconfirmed": unconfirmed}
        finally:
            conn.close()

    return _with_lock(_do)
