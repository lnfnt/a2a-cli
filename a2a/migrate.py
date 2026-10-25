"""Schema migration helper (v0.9.0 §7, G-09).

Records carry a `record_version` in front matter. Older records (pre-v0.9)
have none and are treated as implicit 0.6. `a2a migrate` lists the ones that
need a bump and, with --yes, adds/bumps the field in place. Idempotent.
"""
from __future__ import annotations

import hashlib
import json
import time
from argparse import Namespace
from pathlib import Path
from typing import Any

from . import auditlog, index, util


def _version_key(value: Any) -> tuple[int, ...]:
    parts: list[int] = []
    for part in str(value or "").split("."):
        try:
            parts.append(int(part))
        except (TypeError, ValueError):
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _implicit_version(rec: dict[str, Any]) -> str:
    return "0.6" if rec.get("record_version") is None else str(rec.get("record_version"))


def _scan(target: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        files = index._all_record_files()
    except Exception:
        files = []
    for path in files:
        try:
            rec = util.read_record(path)
        except Exception:
            rec = None
        if not rec:
            continue
        current = _implicit_version(rec)
        if _version_key(current) >= _version_key(target):
            continue
        out.append({
            "path": str(path),
            "id": rec.get("id"),
            "type": rec.get("type"),
            "author": rec.get("author"),
            "from": current,
            "from_version": current,
            "old_version": current,
            "record_version": current,
            "to": target,
            "to_version": target,
            "target": target,
            "target_version": target,
            "action": "add_record_version" if rec.get("record_version") is None else "bump_record_version",
        })
    return out


def cmd_migrate(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    target = str(getattr(args, "to", None) or "0.9").strip() or "0.9"
    yes = bool(getattr(args, "yes", False))
    dry_run = bool(getattr(args, "dry_run", False)) or not yes

    needs = _scan(target)

    if not dry_run:
        applied_paths: list[str] = []
        shas: list[str] = []
        for entry in needs:
            path = Path(entry["path"])
            try:
                text = path.read_text(encoding="utf-8-sig")
                parsed = util.split_front_matter(text)
                if parsed is None:
                    continue
                meta, body = parsed
                meta["record_version"] = target
                new_text = util.join_front_matter(meta, body)
                util.write_atomically(path, new_text)
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
                entry["sha256"] = sha
                entry["applied"] = True
                applied_paths.append(str(path))
                shas.append(sha)
            except Exception:
                entry["applied"] = False
        try:
            auditlog.log("migrate", profile, "records", {
                "target": target,
                "count": len(needs),
                "files": [e["path"] for e in needs],
                "sha256": shas,
                "dry_run": False,
            })
            auditlog.log("schema_migrate", profile, "records", {
                "target": target,
                "count": len(needs),
                "files": [e["path"] for e in needs],
                "sha256": shas,
                "dry_run": False,
            })
        except Exception:
            pass

    applied_count = 0 if dry_run else len(needs)
    data = {
        "dry_run": dry_run,
        "to": target,
        "target": target,
        "target_version": target,
        "to_version": target,
        "record_version": target,
        "record_version_ceiling": target,
        "count": len(needs),
        "needs": len(needs),
        "needs_bump": len(needs),
        "needs_count": len(needs),
        "files": [e["path"] for e in needs],
        "paths": [e["path"] for e in needs],
        "file_paths": [e["path"] for e in needs],
        "ids": [e.get("id") for e in needs],
        "entries": needs,
        "plan": needs,
        "planned": len(needs),
        "planned_count": len(needs),
        "applied": applied_count,
        "applied_count": applied_count,
        "migrated": applied_count,
        "migrated_count": applied_count,
        "updated": applied_count,
        "updated_count": applied_count,
        "no_op": len(needs) == 0,
    }
    util.ok(data, elapsed_ms=int((time.time() - start) * 1000))