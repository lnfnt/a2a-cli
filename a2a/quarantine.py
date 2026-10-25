"""Quarantine for hard-rejected payloads (v0.9.0 §6, G-07)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from argparse import Namespace
from pathlib import Path

from . import access as acl_mod, auditlog, util


def _qdir() -> Path:
    return util.HOME / ".state" / "quarantine"


def _purged_dir() -> Path:
    return util.HOME / ".state" / "quarantine_purged"


def _read_quarantine(path: Path) -> dict | None:
    rec = util.read_record(path)
    if rec is None:
        # fall back to raw
        try:
            return {"id": path.stem, "author": None, "_path": str(path), "_body": path.read_text(encoding="utf-8")}
        except Exception:
            return None
    rec["_path"] = str(path)
    return rec


def quarantine_payload(profile: str, payload: str, hits) -> tuple[str, Path]:
    qdir = _qdir()
    qdir.mkdir(parents=True, exist_ok=True)
    now = util.now_utc()
    sha = hashlib.sha256((payload or "").encode("utf-8", "replace")).hexdigest()
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    qid = f"{stamp}_{sha}"
    path = qdir / f"{qid}.md"
    meta = {
        "id": qid,
        "type": "quarantine",
        "author": profile,
        "access": "L1",
        "created_at": util.utc_iso(now),
        "hits": sorted(set(hits or [])),
        "sha256": sha,
    }
    text = util.join_front_matter(meta, payload or "")
    util.write_atomically(path, text)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    try:
        auditlog.log("secret_quarantined", profile, "quarantine",
                     {"id": qid, "hits": sorted(set(hits or []))})
    except Exception:
        pass
    return qid, path


def _entries(profile: str) -> list[tuple[dict, Path]]:
    out = []
    for path in util.list_files(_qdir()):
        rec = _read_quarantine(path)
        if not rec:
            continue
        # L1 author only
        author = rec.get("author")
        if author is not None and str(author) != str(profile):
            continue
        # may_read gate too
        try:
            if not acl_mod.may_read(rec, profile):
                continue
        except Exception:
            pass
        out.append((rec, path))
    return out


def cmd_quarantine_list(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    entries = []
    for rec, path in _entries(profile):
        entries.append({
            "id": rec.get("id") or path.stem,
            "author": rec.get("author"),
            "hits": rec.get("hits"),
            "created_at": rec.get("created_at"),
            "sha256": rec.get("sha256"),
            "path": str(path),
        })
    util.ok({"entries": entries, "ids": [e["id"] for e in entries],
             "count": len(entries), "quarantine_count": len(entries)},
            elapsed_ms=int((time.time() - start) * 1000))


def _find(profile: str, qid: str) -> tuple[dict, Path] | None:
    for rec, path in _entries(profile):
        if (rec.get("id") or path.stem) == qid or path.stem == qid or path.stem.startswith(qid):
            return rec, path
    # allow partial id match
    matches = [(rec, path) for rec, path in _entries(profile)
               if qid and (path.stem == qid or path.stem.startswith(qid) or qid in path.stem)]
    if len(matches) == 1:
        return matches[0]
    return None


def cmd_quarantine_show(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    qid = args.id
    found = _find(profile, qid)
    if not found:
        util.fail(util.EXIT_USAGE_ERROR, "quarantine_not_found", {"id": qid})
    rec, path = found
    author = rec.get("author")
    if author is not None and str(author) != str(profile):
        util.fail(util.EXIT_USAGE_ERROR, "access_denied", {"id": qid})
    try:
        if not acl_mod.may_read(rec, profile):
            util.fail(util.EXIT_USAGE_ERROR, "access_denied", {"id": qid})
    except Exception:
        pass
    content = rec.get("_body", "")
    if content is None:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            content = ""
    util.ok({"id": rec.get("id") or path.stem, "author": author,
             "hits": rec.get("hits"), "created_at": rec.get("created_at"),
             "content": content, "text": content, "body": content,
             "payload": content, "path": str(path)},
            elapsed_ms=int((time.time() - start) * 1000))


def cmd_quarantine_purge(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    yes = bool(getattr(args, "yes", False))
    dry_run = not yes
    if getattr(args, "dry_run", False):
        dry_run = True

    qdir = _qdir()
    purged_dir = _purged_dir()
    targets = []
    for rec, path in _entries(profile):
        targets.append((rec, path))

    purged = 0
    moved = 0
    ids = [ (rec.get("id") or path.stem) for rec, path in targets]
    entries = [{"id": rec.get("id") or path.stem, "author": rec.get("author"),
                "path": str(path)} for rec, path in targets]
    if not dry_run:
        purged_dir.mkdir(parents=True, exist_ok=True)
        for rec, path in targets:
            dest = purged_dir / path.name
            if dest.exists():
                stem = path.stem
                suffix = path.suffix
                i = 1
                while (purged_dir / f"{stem}_{i}{suffix}").exists():
                    i += 1
                dest = purged_dir / f"{stem}_{i}{suffix}"
            try:
                shutil.move(str(path), str(dest))
                moved += 1
            except Exception:
                try:
                    path.unlink()
                    moved += 1
                except Exception:
                    pass
        purged = moved

    util.ok({"purged": purged, "purged_count": purged, "moved": moved,
             "moved_count": moved, "removed": purged, "removed_count": purged,
             "count": len(targets), "ids": ids, "entries": entries,
             "dry_run": dry_run},
            elapsed_ms=int((time.time() - start) * 1000))
