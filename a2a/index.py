"""Derived index management."""

from __future__ import annotations

import sqlite3
import time
from argparse import Namespace
from pathlib import Path

from . import schema, util


def db_path() -> Path:
    return util.HOME / ".state" / "index.sqlite3"


def init_db() -> None:
    path = db_path()
    util.ensure_dir(path)
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS records (
            id TEXT,
            type TEXT,
            author TEXT,
            access TEXT,
            created_at TEXT,
            path TEXT PRIMARY KEY,
            body TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_type ON records(type);
        CREATE INDEX IF NOT EXISTS idx_author ON records(author);
        CREATE INDEX IF NOT EXISTS idx_access ON records(access);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(path, body);
        """
    )
    conn.commit()
    conn.close()


def rebuild() -> dict:
    init_db()
    path = db_path()
    conn = sqlite3.connect(str(path))
    conn.execute("DELETE FROM records")
    conn.execute("DELETE FROM fts")

    counts = {}
    for rec_path in _all_record_files():
        rec = util.read_record(rec_path)
        if not rec:
            continue
        access = rec.get("access")
        if access not in schema.ACCESS_LEVELS:
            continue
        if not access_matches_path(rec, rec_path):
            continue
        type_ = rec.get("type", "unknown")
        counts[type_] = counts.get(type_, 0) + 1
        conn.execute(
            "INSERT INTO records (id, type, author, access, created_at, path, body) VALUES (?,?,?,?,?,?,?)",
            (
                rec.get("id"),
                type_,
                rec.get("author"),
                rec.get("access"),
                rec.get("created_at"),
                str(rec_path),
                rec.get("_body", ""),
            ),
        )
        conn.execute(
            "INSERT INTO fts (path, body) VALUES (?, ?)",
            (str(rec_path), _searchable_text(rec)),
        )
    conn.commit()
    conn.close()
    return counts


def increment() -> dict:
    """Incrementally sync the derived index with files on disk (no full wipe)."""
    init_db()
    path = db_path()
    conn = sqlite3.connect(str(path))
    counts = {}
    disk = [str(p) for p in _all_record_files()]
    disk_set = set(disk)
    # remove rows for files that disappeared
    cur = conn.cursor()
    cur.execute("SELECT path FROM records")
    for (p,) in cur.fetchall():
        if p not in disk_set:
            conn.execute("DELETE FROM records WHERE path=?", (p,))
            conn.execute("DELETE FROM fts WHERE path=?", (p,))
    for rec_path in _all_record_files():
        rec = util.read_record(rec_path)
        if not rec:
            conn.execute("DELETE FROM records WHERE path=?", (str(rec_path),))
            conn.execute("DELETE FROM fts WHERE path=?", (str(rec_path),))
            continue
        access = rec.get("access")
        if access not in schema.ACCESS_LEVELS:
            conn.execute("DELETE FROM records WHERE path=?", (str(rec_path),))
            conn.execute("DELETE FROM fts WHERE path=?", (str(rec_path),))
            continue
        if not access_matches_path(rec, rec_path):
            conn.execute("DELETE FROM records WHERE path=?", (str(rec_path),))
            conn.execute("DELETE FROM fts WHERE path=?", (str(rec_path),))
            continue
        type_ = rec.get("type", "unknown")
        counts[type_] = counts.get(type_, 0) + 1
        conn.execute("DELETE FROM records WHERE path=?", (str(rec_path),))
        conn.execute("DELETE FROM fts WHERE path=?", (str(rec_path),))
        conn.execute(
            "INSERT INTO records (id, type, author, access, created_at, path, body) VALUES (?,?,?,?,?,?,?)",
            (
                rec.get("id"),
                type_,
                rec.get("author"),
                rec.get("access"),
                rec.get("created_at"),
                str(rec_path),
                rec.get("_body", ""),
            ),
        )
        conn.execute(
            "INSERT INTO fts (path, body) VALUES (?, ?)",
            (str(rec_path), _searchable_text(rec)),
        )
    conn.commit()
    conn.close()
    return counts


def access_matches_path(rec: dict, path: Path) -> bool:
    """Detect in-place access tampering: thought paths embed their access level."""
    access = rec.get("access")
    if access not in schema.ACCESS_LEVELS:
        return False
    path_str = str(path)
    if "/thoughts/" in path_str:
        expected = f"/{access.lower()}/"
        return expected in path_str
    return True


def _all_record_files() -> list[Path]:
    files = []
    for sub in ("journal", "thoughts", "user-feed", "locks", "alerts", "records", "status", "tasks"):
        files.extend(util.list_files(util.HOME / sub))
    # mail: index only canonical inbox copies, not sender outbox duplicates
    if (util.HOME / "mail").exists():
        for profile_dir in (util.HOME / "mail").iterdir():
            if not profile_dir.is_dir():
                continue
            inbox = profile_dir / "inbox"
            archive = profile_dir / "archive"
            files.extend(util.list_files(inbox))
            files.extend(util.list_files(archive))
    return files


def _searchable_text(rec: dict) -> str:
    parts = []
    for key in ("what_done", "subject", "topic", "task", "reason", "_body"):
        val = rec.get(key)
        if val:
            parts.append(str(val))
    return "\n".join(parts)


def cmd_index(args: Namespace) -> None:
    start = time.time()
    if getattr(args, "rebuild", False):
        counts = rebuild()
        util.ok({"rebuilt": True, "counts": counts}, elapsed_ms=int((time.time() - start) * 1000))
    elif getattr(args, "increment", False) or getattr(args, "update", False) or getattr(args, "incremental", False):
        counts = increment()
        util.ok({"incremented": True, "counts": counts}, elapsed_ms=int((time.time() - start) * 1000))
    else:
        path = db_path()
        exists = path.exists()
        util.ok({"index_exists": exists, "path": str(path)}, elapsed_ms=int((time.time() - start) * 1000))
