"""Resource lock commands."""

from __future__ import annotations

import fcntl
import os
import time
from argparse import Namespace
from datetime import datetime, timedelta, timezone

from . import schema, scrubber, util
from . import access as acl_mod

_SCOPES = {"mail", "journal", "tasks"}


def _scope_of(rec: dict) -> str | None:
    return rec.get("scope") or rec.get("target")


def _parse_ttl(raw) -> int:
    s = str(raw).strip()
    try:
        if s.endswith("m"):
            return int(s[:-1]) * 60
        if s.endswith("h"):
            return int(s[:-1]) * 3600
        if s.endswith("d"):
            return int(s[:-1]) * 86400
        return int(s)
    except Exception:
        util.fail(util.EXIT_INVALID_RECORD, "invalid_ttl", {"value": raw})


def ensure_scope_writable(scope: str, profile: str) -> None:
    """Raise (via util.fail) if an active lock on `scope` is held by another profile."""
    now = util.now_utc()
    for rec, path in _iter_locks():
        if _scope_of(rec) != scope:
            continue
        try:
            exp = util.parse_utc(rec["expires"])
        except Exception:
            continue
        if exp <= now:
            continue
        if rec.get("author") == profile:
            continue
        ttl_left = int((exp - now).total_seconds())
        if ttl_left < 0:
            ttl_left = 0
        util.fail_hint(
            util.EXIT_ERROR,
            "locked_by_other",
            "scope is locked by another profile; wait for expiry or ask holder to release",
            "a2a lock release --scope " + str(scope),
            {
                "scope": scope,
                "holder": rec.get("author"),
                "expires": rec.get("expires"),
                "ttl_left": ttl_left,
                "ttl-left": ttl_left,
                "ttl_left_seconds": ttl_left,
                "id": rec.get("id"),
            },
        )


def cmd_lock_set(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    target = args.target
    reason = args.reason or ""

    # parse expire shorthand like 15m, 1h, 2026-09-25T12:00:00Z
    expire = args.expire
    try:
        if expire.endswith("m"):
            minutes = int(expire[:-1])
            expires_dt = util.now_utc() + timedelta(minutes=minutes)
        elif expire.endswith("h"):
            hours = int(expire[:-1])
            expires_dt = util.now_utc() + timedelta(hours=hours)
        elif expire.endswith("d"):
            days = int(expire[:-1])
            expires_dt = util.now_utc() + timedelta(days=days)
        else:
            expires_dt = util.parse_utc(expire)
    except Exception:
        util.fail(util.EXIT_INVALID_RECORD, "invalid_expire")

    # Serialize lock creation across processes to eliminate TOCTOU between
    # the conflict scan and the atomic file write.
    mutex_path = util.HOME / "locks" / ".lock_mutex"
    util.ensure_dir(mutex_path)
    fd = os.open(str(mutex_path), os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)

        # check conflicts: active lock by another profile on same target
        now = util.now_utc()
        for rec, path in _iter_locks():
            if rec.get("target") == target and rec.get("author") != profile:
                try:
                    exp = util.parse_utc(rec["expires"])
                except Exception:
                    continue
                if exp > now:
                    util.fail(
                        util.EXIT_ERROR,
                        "lock_conflict",
                        {"target": target, "holder": rec.get("author"), "expires": rec["expires"]},
                    )

        access = args.access or schema.DEFAULT_ACCESS["lock"]
        meta = {
            "type": "lock",
            "author": profile,
            "access": access,
            "target": target,
            "reason": reason,
            "expires": util.utc_iso(expires_dt),
        }
        schema.apply_defaults(meta, "lock")

        ok, err = schema.validate(meta, "")
        if not ok:
            util.fail(util.EXIT_INVALID_RECORD, err)

        body = f"{reason}\n"
        text = util.join_front_matter(meta, body)
        dirty, hits = scrubber.check(text)
        if dirty:
            try:
                from . import quarantine as _q
                _q.quarantine_payload(profile, text, hits)
            except Exception:
                pass
            util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", "a2a lock acquire --scope mail --ttl 60", {"hits": hits})

        created = util.parse_utc(meta["created_at"])
        path = util.record_path("lock", profile, access, created, meta["id"])
        util.write_atomically(path, text)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    util.ok({"id": meta["id"], "target": target, "expires": meta["expires"]}, elapsed_ms=int((time.time() - start) * 1000))


def cmd_lock_list(args: Namespace) -> None:
    start = time.time()
    now = util.now_utc()
    active = []
    expired = []
    profile = util.resolve_profile(args)
    for rec, path in _iter_locks():
        # v0.7.0 §5: foreign L1 locks are not shown to non-owners
        if not acl_mod.may_read(rec, profile):
            continue
        try:
            exp = util.parse_utc(rec["expires"])
        except Exception:
            continue
        entry = {
            "id": rec["id"],
            "author": rec.get("author"),
            "target": rec.get("target"),
            "reason": rec.get("reason"),
            "expires": rec["expires"],
        }
        if exp > now:
            active.append(entry)
        else:
            expired.append(entry)
    util.ok({"active": active, "expired": expired}, elapsed_ms=int((time.time() - start) * 1000))


def cmd_lock_clean(args: Namespace) -> None:
    """Remove expired lock files. Doctor reports them; this explicitly cleans."""
    start = time.time()
    now = util.now_utc()
    removed = []
    for rec, path in _iter_locks():
        try:
            exp = util.parse_utc(rec["expires"])
        except Exception:
            continue
        if exp < now:
            path.unlink(missing_ok=True)
            removed.append(rec["id"])
    util.ok({"removed": len(removed), "ids": removed}, elapsed_ms=int((time.time() - start) * 1000))


def cmd_lock_acquire(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    scope = getattr(args, "scope", None)
    if scope not in _SCOPES:
        util.fail_hint(util.EXIT_USAGE_ERROR, "invalid_choice",
                      "scope must be one of: mail, journal, tasks",
                      "a2a lock acquire --scope mail --ttl 60",
                      {"field": "scope", "value": scope, "allowed": sorted(_SCOPES)})
    ttl = _parse_ttl(getattr(args, "ttl", None))
    now = util.now_utc()
    expires_dt = now + timedelta(seconds=ttl)

    # conflict: active lock by another profile on same scope
    for rec, path in _iter_locks():
        if _scope_of(rec) != scope:
            continue
        try:
            exp = util.parse_utc(rec["expires"])
        except Exception:
            continue
        if exp <= now:
            continue
        if rec.get("author") != profile:
            ttl_left = int((exp - now).total_seconds())
            if ttl_left < 0:
                ttl_left = 0
            util.fail(util.EXIT_ERROR, "lock_conflict",
                      {"scope": scope, "holder": rec.get("author"),
                       "expires": rec.get("expires"), "ttl_left": ttl_left})

    access = getattr(args, "access", None) or schema.DEFAULT_ACCESS["lock"]
    reason = getattr(args, "reason", "") or ""
    meta = {
        "type": "lock",
        "author": profile,
        "access": access,
        "target": scope,
        "scope": scope,
        "reason": reason,
        "expires": util.utc_iso(expires_dt),
        "ttl": ttl,
    }
    schema.apply_defaults(meta, "lock")
    ok, err = schema.validate(meta, "")
    if not ok:
        util.fail(util.EXIT_INVALID_RECORD, err)
    body = f"{reason}\n"
    text = util.join_front_matter(meta, body)
    dirty, hits = scrubber.check(text)
    if dirty:
        try:
            from . import quarantine as _q
            _q.quarantine_payload(profile, text, hits)
        except Exception:
            pass
        util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", "a2a lock acquire --scope mail --ttl 60", {"hits": hits})
    created = util.parse_utc(meta["created_at"])
    path = util.record_path("lock", profile, access, created, meta["id"])
    util.write_atomically(path, text)
    util.ok({"id": meta["id"], "scope": scope, "target": scope,
             "expires": meta["expires"], "ttl": ttl},
            elapsed_ms=int((time.time() - start) * 1000))


def cmd_lock_release(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    scope = getattr(args, "scope", None)
    if scope not in _SCOPES:
        util.fail_hint(util.EXIT_USAGE_ERROR, "invalid_choice",
                      "scope must be one of: mail, journal, tasks",
                      "a2a lock release --scope mail",
                      {"field": "scope", "value": scope, "allowed": sorted(_SCOPES)})
    removed = []
    for rec, path in list(_iter_locks()):
        if _scope_of(rec) != scope:
            continue
        if rec.get("author") != profile:
            continue
        try:
            path.unlink(missing_ok=True)
        except Exception:
            continue
        removed.append(rec.get("id"))
    util.ok({"scope": scope, "released": len(removed), "removed": len(removed),
             "ids": removed, "count": len(removed)},
            elapsed_ms=int((time.time() - start) * 1000))


def _iter_locks():
    for path in util.list_files(util.HOME / "locks"):
        rec = util.read_record(path)
        if rec:
            yield rec, path
