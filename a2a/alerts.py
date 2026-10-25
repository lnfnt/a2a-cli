"""Health alert commands."""

from __future__ import annotations

import shutil
import time
from argparse import Namespace
from datetime import timedelta

from . import schema, scrubber, util
from . import access as acl_mod


def cmd_alert_set(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    target = args.target
    severity = args.severity
    active_until = args.active_until
    try:
        if active_until.endswith("m"):
            minutes = int(active_until[:-1])
            until_dt = util.now_utc() + timedelta(minutes=minutes)
        elif active_until.endswith("h"):
            hours = int(active_until[:-1])
            until_dt = util.now_utc() + timedelta(hours=hours)
        elif active_until.endswith("d"):
            days = int(active_until[:-1])
            until_dt = util.now_utc() + timedelta(days=days)
        else:
            until_dt = util.parse_utc(active_until)
    except Exception:
        util.fail(util.EXIT_INVALID_RECORD, "invalid_active_until")

    access = args.access or schema.DEFAULT_ACCESS["alert"]
    meta = {
        "type": "alert",
        "author": profile,
        "access": access,
        "target": target,
        "severity": severity,
        "active_until": util.utc_iso(until_dt),
    }
    schema.apply_defaults(meta, "alert")

    ok, err = schema.validate(meta, args.body or "")
    if not ok:
        util.fail(util.EXIT_INVALID_RECORD, err)

    body = args.body or ""
    text = util.join_front_matter(meta, body)
    dirty, hits = scrubber.check(text)
    if dirty:
        try:
            from . import quarantine as _q
            _q.quarantine_payload(profile, text, hits)
        except Exception:
            pass
        util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", "a2a alert set --target x --severity info --active-until 2026-12-31 body", {"hits": hits})

    created = util.parse_utc(meta["created_at"])
    path = util.record_path("alert", profile, access, created, meta["id"])
    util.write_atomically(path, text)
    util.ok({"id": meta["id"], "target": target, "severity": severity, "active_until": meta["active_until"]}, elapsed_ms=int((time.time() - start) * 1000))


def cmd_alert_list(args: Namespace) -> None:
    start = time.time()
    now = util.now_utc()
    profile = util.resolve_profile(args)
    active = []
    for rec, path in _iter_alerts():
        # v0.5.0 (J-05): единый read-гейт; без acl-поля — прежняя семантика L1
        if not acl_mod.may_read(rec, profile):
            continue
        try:
            until = util.parse_utc(rec["active_until"])
        except Exception:
            continue
        if until > now:
            active.append({
                "id": rec["id"],
                "author": rec.get("author"),
                "target": rec.get("target"),
                "severity": rec.get("severity"),
                "active_until": rec["active_until"],
            })
    util.ok({"active": active}, elapsed_ms=int((time.time() - start) * 1000))


def _iter_alerts():
    for path in util.list_files(util.HOME / "alerts"):
        rec = util.read_record(path)
        if rec:
            yield rec, path


def cmd_alert_purge_expired(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    now = util.now_utc()
    dry_run = not bool(getattr(args, "yes", False))
    if getattr(args, "dry_run", False):
        dry_run = True

    expired = []
    moved = 0
    expired_dir = util.HOME / ".state" / "expired"
    for rec, path in _iter_alerts():
        if not acl_mod.may_read(rec, profile):
            continue
        try:
            until = util.parse_utc(rec["active_until"])
        except Exception:
            continue
        if until > now:
            continue
        entry = {
            "id": rec.get("id"),
            "author": rec.get("author"),
            "target": rec.get("target"),
            "severity": rec.get("severity"),
            "active_until": rec.get("active_until"),
            "path": str(path),
        }
        expired.append(entry)
        if not dry_run:
            expired_dir.mkdir(parents=True, exist_ok=True)
            dest = expired_dir / path.name
            if dest.exists():
                stem = path.stem
                suffix = path.suffix
                i = 1
                while (expired_dir / f"{stem}_{i}{suffix}").exists():
                    i += 1
                dest = expired_dir / f"{stem}_{i}{suffix}"
            shutil.move(str(path), str(dest))
            entry["moved_to"] = str(dest)
            moved += 1

    ids = [e.get("id") for e in expired]
    util.ok({
        "expired": expired,
        "expired_ids": ids,
        "ids": ids,
        "purged": 0 if dry_run else moved,
        "purged_count": 0 if dry_run else moved,
        "moved": moved,
        "moved_count": moved,
        "removed": 0 if dry_run else moved,
        "removed_count": 0 if dry_run else moved,
        "count": len(expired),
        "expired_count": len(expired),
        "dry_run": dry_run,
    }, elapsed_ms=int((time.time() - start) * 1000))
