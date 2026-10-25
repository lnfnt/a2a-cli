"""Retention pruning (v0.9.0 §8, H-03)."""

from __future__ import annotations

import shutil
import time
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import index, util
from . import access as acl_mod


_KIND_DIRS = {
    "journal": ["journal"],
    "mail": ["mail"],
    "thought": ["thoughts"],
    "thoughts": ["thoughts"],
    "task": ["tasks"],
    "tasks": ["tasks"],
    "alert": ["alerts"],
    "alerts": ["alerts"],
    "status": ["status"],
    "user": ["user-feed"],
    "user-note": ["user-feed"],
    "user-feed": ["user-feed"],
    "lock": ["locks"],
    "locks": ["locks"],
    "all": ["journal", "mail", "thoughts", "tasks", "alerts", "status", "user-feed", "locks"],
}


def _parse_older_than(value) -> timedelta:
    v = str(value or "").strip().lower()
    if v.endswith("d"):
        v = v[:-1]
    try:
        days = int(v)
    except Exception:
        raise ValueError("invalid_older_than")
    if days < 0:
        raise ValueError("invalid_older_than")
    return timedelta(days=days)


def _record_ts(rec: dict, path: Path) -> datetime | None:
    for key in ("updated_at", "created_at"):
        val = rec.get(key)
        if val:
            try:
                return util.parse_utc(val)
            except Exception:
                pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except Exception:
        return None



def _iso_value(val):
    if isinstance(val, datetime):
        return util.utc_iso(val)
    if isinstance(val, str):
        try:
            return util.utc_iso(util.parse_utc(val))
        except Exception:
            return val
    return str(val) if val is not None else None


def _immune(rec: dict, path: Path, kind: str) -> str | None:
    k = kind.lower()
    if k in ("mail",):
        parts = path.parts
        status = rec.get("msg_status")
        if "inbox" in parts and status in (None, "", "unread"):
            return "immune_unread"
        return None
    if k in ("task", "tasks"):
        status = str(rec.get("status") or "").strip().lower()
        if status not in ("done", "cancelled", "rejected", "archived"):
            return "immune_active"
        return None
    return None


def _files_for_kind(kind: str) -> list[Path]:
    kind = kind.strip().lower()
    dirs = _KIND_DIRS.get(kind)
    if dirs is None:
        # allow comma list handled by caller; unknown -> empty
        return []
    out: list[Path] = []
    for d in dirs:
        base = util.HOME / d
        if d == "mail":
            for box in ("inbox", "outbox"):
                for profile_dir in (base.iterdir() if base.exists() else []):
                    if profile_dir.is_dir():
                        out.extend(util.list_files(profile_dir / box))
            # also any stray mail files
            out.extend(util.list_files(base))
        else:
            out.extend(util.list_files(base))
    # dedupe
    seen = set()
    uniq = []
    for p in out:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return sorted(uniq)


def cmd_prune(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    older_than = getattr(args, "older_than", None) or "365d"
    try:
        delta = _parse_older_than(older_than)
    except Exception:
        util.fail(util.EXIT_USAGE_ERROR, "invalid_older_than", {"value": older_than})
        return
    cutoff = util.now_utc() - delta

    kind_raw = getattr(args, "kind", None) or "journal"
    kinds = [k.strip() for k in str(kind_raw).split(",") if k.strip()]
    if not kinds:
        kinds = ["journal"]

    yes = bool(getattr(args, "yes", False))
    dry_run = bool(getattr(args, "dry_run", False)) or not yes

    entries = []
    moved_paths: list[str] = []
    moved = 0
    immune_count = 0
    eligible_count = 0

    archive_dir = util.HOME / ".state" / "archive"

    for kind in kinds:
        for path in _files_for_kind(kind):
            rec = util.read_record(path)
            if not rec:
                continue
            try:
                if not acl_mod.may_read(rec, profile):
                    continue
            except Exception:
                pass
            ts = _record_ts(rec, path)
            if ts is None:
                continue
            if ts >= cutoff:
                continue
            reason = _immune(rec, path, kind)
            immune = reason is not None
            if immune:
                immune_count += 1
            else:
                eligible_count += 1
            entry = {
                "path": str(path),
                "id": rec.get("id"),
                "kind": kind,
                "type": rec.get("type"),
                "author": rec.get("author"),
                "status": rec.get("status") or rec.get("msg_status"),
                "created_at": _iso_value(rec.get("created_at")),
                "ts": util.utc_iso(ts),
                "age_days": int((util.now_utc() - ts).total_seconds() // 86400),
                "reason": reason or "eligible",
                "immune": immune,
            }
            entries.append(entry)
            if immune:
                continue
            if dry_run:
                continue
            archive_dir.mkdir(parents=True, exist_ok=True)
            dest = archive_dir / path.name
            if dest.exists():
                stem = path.stem
                suffix = path.suffix
                i = 1
                while (archive_dir / f"{stem}_{i}{suffix}").exists():
                    i += 1
                dest = archive_dir / f"{stem}_{i}{suffix}"
            try:
                shutil.move(str(path), str(dest))
                moved += 1
                moved_paths.append(str(dest))
                entry["moved_to"] = str(dest)
                entry["archived_to"] = str(dest)
            except Exception:
                entry["move_failed"] = True

    if (not dry_run) and moved:
        try:
            index.rebuild()
        except Exception:
            pass

    ids = [e.get("id") for e in entries]
    eligible_entries = [e for e in entries if not e.get("immune")]
    util.ok({
        "dry_run": dry_run,
        "yes": yes,
        "older_than": older_than,
        "older_than_days": delta.days,
        "cutoff": util.utc_iso(cutoff),
        "threshold": util.utc_iso(cutoff),
        "kinds": kinds,
        "kind": kinds[0] if len(kinds) == 1 else kinds,
        "count": len(entries),
        "entries": entries,
        "items": entries,
        "files": [e["path"] for e in entries],
        "file_paths": [e["path"] for e in entries],
        "ids": ids,
        "moved": moved,
        "moved_count": moved,
        "moved_paths": moved_paths,
        "archived": moved,
        "archived_count": moved,
        "archived_files": moved_paths,
        "archived_paths": moved_paths,
        "archived_to": (moved_paths[0] if moved_paths else None),
        "immune_count": immune_count,
        "eligible_count": eligible_count,
        "candidate_count": len(entries),
        "would_archive": eligible_count,
        "would_move": eligible_count,
    }, elapsed_ms=int((time.time() - start) * 1000))
