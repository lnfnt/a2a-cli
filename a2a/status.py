"""Status and DND commands."""

from __future__ import annotations

import time
from argparse import Namespace
from datetime import datetime, timedelta, timezone

from . import schema, scrubber, util


def cmd_status(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    if args.state:
        return _status_set(args, profile, start)
    return _status_show(args, profile, start)


def _status_set(args: Namespace, profile: str, start: float) -> None:
    heartbeat = bool(getattr(args, "heartbeat", False))
    state = getattr(args, "state", None)
    if state is None:
        if heartbeat:
            existing = None
            spath = util.HOME / "status" / f"{profile}.md"
            if spath.exists():
                existing = util.read_record(spath)
            state = (existing.get("state") if existing else None) or "idle"
        else:
            util.fail(util.EXIT_INVALID_RECORD, "invalid_state")
    if state not in schema.VALID_STATES:
        util.fail(util.EXIT_INVALID_RECORD, "invalid_state")

    until = None
    if args.until:
        try:
            until = util.parse_utc(args.until)
        except Exception:
            try:
                # allow HH:MM relative to today
                hour, minute = map(int, args.until.split(":"))
                until = datetime.now(timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0)
                if until < util.now_utc():
                    until += timedelta(days=1)
            except Exception:
                util.fail(util.EXIT_INVALID_RECORD, "invalid_until")

    access = args.access or schema.DEFAULT_ACCESS["status"]
    meta = {
        "type": "status",
        "author": profile,
        "access": access,
        "state": state,
        "note": args.note or None,
        "until": util.utc_iso(until) if until else None,
        "heartbeat": util.utc_iso(),
        "heartbeat_at": util.utc_iso(),
        "updated_at": util.utc_iso(),
    }
    schema.apply_defaults(meta, "status")

    ok, err = schema.validate(meta, "")
    if not ok:
        util.fail(util.EXIT_INVALID_RECORD, err)

    text = util.join_front_matter(meta, "")
    dirty, hits = scrubber.check(text)
    if dirty:
        try:
            from . import quarantine as _q
            _q.quarantine_payload(profile, text, hits)
        except Exception:
            pass
        util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", "a2a status set --state ok", {"hits": hits})

    path = util.record_path("status", profile, access, util.parse_utc(meta["created_at"]), meta["id"])
    # status file overwrites
    util.write_atomically(path, text)
    util.ok({"id": meta["id"], "state": state, "until": meta["until"],
            "heartbeat": meta["heartbeat"], "heartbeat_at": meta["heartbeat_at"],
            "age": 0, "age_seconds": 0},
           elapsed_ms=int((time.time() - start) * 1000))


def _status_show(args: Namespace, profile: str, start: float) -> None:
    now = util.now_utc()
    entries = []
    for path in util.list_files(util.HOME / "status"):
        rec = util.read_record(path)
        if not rec:
            continue
        hb = rec.get("heartbeat") or rec.get("heartbeat_at") or rec.get("updated_at") or rec.get("created_at")
        age_seconds = None
        try:
            age_seconds = int((now - util.parse_utc(hb)).total_seconds())
        except Exception:
            age_seconds = None
        entries.append({
            "id": rec.get("id"),
            "author": rec.get("author"),
            "profile": rec.get("author"),
            "state": rec.get("state"),
            "note": rec.get("note"),
            "until": rec.get("until"),
            "heartbeat": hb,
            "heartbeat_at": hb,
            "updated_at": rec.get("created_at"),
            "created_at": rec.get("created_at"),
            "age": age_seconds,
            "age_seconds": age_seconds,
            "age_minutes": (age_seconds // 60) if age_seconds is not None else None,
        })
    util.ok({"count": len(entries), "entries": entries}, elapsed_ms=int((time.time() - start) * 1000))


def cmd_fleet(args: Namespace) -> None:
    start = time.time()
    try:
        from . import access as _acl
        names = _acl.load_manifest_names()
    except Exception:
        names = None
    if not names:
        try:
            from . import access as _acl
            names = set(_acl.DEFAULT_PEER_PROFILES)
        except Exception:
            names = {"coordinator", "colleague", "user-profile", "support", "agent-profile", "infra"}
    # drop channel-like entries just in case
    names = {n for n in names if n and not n.startswith("user-")}
    ttl_raw = getattr(args, "ttl", None)
    if ttl_raw is None:
        ttl_seconds = 24 * 3600
    else:
        try:
            from . import locks as _locks_mod
            ttl_seconds = _locks_mod._parse_ttl(ttl_raw)
        except Exception:
            ttl_seconds = 24 * 3600
    now = util.now_utc()
    profiles = []
    for name in sorted(names):
        path = util.HOME / "status" / f"{name}.md"
        rec = util.read_record(path) if path.exists() else None
        hb = None
        state = None
        note = None
        until = None
        if rec:
            hb = rec.get("heartbeat") or rec.get("heartbeat_at") or rec.get("updated_at") or rec.get("created_at")
            state = rec.get("state")
            note = rec.get("note")
            until = rec.get("until")
        age_seconds = None
        if hb is not None:
            try:
                age_seconds = int((now - util.parse_utc(hb)).total_seconds())
            except Exception:
                age_seconds = None
        # DND active
        dnd_active = False
        if state == "dnd":
            if until:
                try:
                    dnd_active = util.parse_utc(until) > now
                except Exception:
                    dnd_active = True
            else:
                dnd_active = True
        stale = False
        if age_seconds is not None and age_seconds > ttl_seconds:
            stale = True
        entry = {
            "profile": name,
            "name": name,
            "author": name,
            "mode": state,
            "state": state,
            "note": note,
            "heartbeat": hb,
            "heartbeat_at": hb,
            "heartbeat_age": age_seconds,
            "heartbeat_age_seconds": age_seconds,
            "age": age_seconds,
            "age_seconds": age_seconds,
            "age_minutes": (age_seconds // 60) if age_seconds is not None else None,
            "dnd": dnd_active,
            "dnd_active": dnd_active,
            "dnd_until": until,
            "stale": stale,
            "stale_flag": stale,
            "stale_status": stale,
        }
        profiles.append(entry)
    util.ok({"count": len(profiles), "profiles": profiles, "entries": profiles},
            elapsed_ms=int((time.time() - start) * 1000))


def cmd_dnd(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    if args.dnd_action == "set":
        return _dnd_set(args, profile, start)
    if args.dnd_action == "clear":
        return _dnd_clear(args, profile, start)
    return _dnd_check(args, profile, start)


def _dnd_set(args: Namespace, profile: str, start: float) -> None:
    args.state = "dnd"
    args.note = None
    if not args.until:
        util.fail(util.EXIT_INVALID_RECORD, "dnd_requires_until")
    return _status_set(args, profile, start)


def _dnd_clear(args: Namespace, profile: str, start: float) -> None:
    args.state = "idle"
    args.note = None
    args.until = None
    args.access = None
    return _status_set(args, profile, start)


def cmd_onboard(args: Namespace) -> None:
    import contextlib
    import io
    import json
    import os

    start = time.time()

    target = getattr(args, "profile", None)
    if not target:
        target = getattr(args, "as_", None)
    if not target:
        target = os.environ.get("A2A_PROFILE") or os.environ.get("HERMES_PROFILE")
    if not target:
        target = "default"
    if not util.validate_profile_name(str(target)):
        util.fail(util.EXIT_USAGE_ERROR, "invalid_profile_name")
    target = str(target)

    home = util.HOME
    changed = False
    created_dirs: list[str] = []

    def _ensure_dir(p: Path) -> None:
        nonlocal changed
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created_dirs.append(str(p))
            changed = True
        elif not p.is_dir():
            util.fail(util.EXIT_USAGE_ERROR, "not_a_directory", {"path": str(p)})
        else:
            p.mkdir(parents=True, exist_ok=True)

    # core bus directories
    for p in (
        home,
        home / ".state",
        home / "journal",
        home / "tasks",
        home / "alerts",
        home / "locks",
        home / "status",
        home / "user-feed",
        home / "records",
        home / "records" / "handoffs",
        home / "mail" / target,
        home / "mail" / target / "inbox",
        home / "mail" / target / "outbox",
        home / "thoughts" / target,
        home / "thoughts" / target / "l1",
        home / "thoughts" / target / "l2",
        home / "thoughts" / target / "l3",
    ):
        _ensure_dir(p)

    # config surfaces: default profile
    config_candidates = [
        home / "config.json",
        home / ".config.json",
        home / "default_profile",
        home / ".profile",
        home / ".state" / "config.json",
        home / ".state" / "default_profile",
        home / ".a2a" / "config.json",
        home / ".a2a" / "default_profile",
    ]
    for p in config_candidates:
        if p.name.endswith(".json"):
            content = json.dumps({"default_profile": target}, ensure_ascii=False, indent=2) + "\n"
        else:
            content = target + "\n"
        _ensure_dir(p.parent)
        if (not p.exists()) or (p.read_text(encoding="utf-8") if p.is_file() else "") != content:
            util.write_atomically(p, content)
            changed = True

    # profiles manifest upsert (governed buses)
    manifest_path = home / "profiles.json"
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            data = None
        if isinstance(data, dict):
            profiles = data.get("profiles")
            if isinstance(profiles, list):
                names = {e.get("name") if isinstance(e, dict) else e for e in profiles}
                if target not in names:
                    profiles.append({"name": target, "role": "peer-agent"})
                    data["profiles"] = profiles
                    util.write_atomically(manifest_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                    changed = True

    # run doctor
    dargs = Namespace(**vars(args)) if hasattr(args, "__dict__") else Namespace()
    # ensure attrs
    for k, v in {
        "command": "doctor",
        "profile": target,
        "ttl": getattr(args, "ttl", None),
        "artifacts": False,
        "doctor_command": None,
        "status_command": None,
    }.items():
        setattr(dargs, k, v)
    buf = io.StringIO()
    doctor_rc = 0
    doctor_json: dict = {}
    with contextlib.redirect_stdout(buf):
        try:
            from . import doctor as _doctor_mod
            _doctor_mod.cmd_doctor(dargs)
        except SystemExit as e:
            doctor_rc = int(e.code) if isinstance(e.code, int) else 0
    raw = buf.getvalue().strip()
    if raw:
        try:
            doctor_json = json.loads(raw)
        except Exception:
            doctor_json = {"raw": raw}
    doctor_data = doctor_json.get("data", doctor_json) if isinstance(doctor_json, dict) else {}
    doctor_status = None
    if isinstance(doctor_data, dict):
        doctor_status = doctor_data.get("status")
    if doctor_status is None and isinstance(doctor_json, dict):
        doctor_status = doctor_json.get("status")
    if doctor_status is None:
        doctor_status = "ok" if doctor_rc == 0 else "degraded"

    next_commands = [
        f"a2a --profile {target} status set --state idle --note ready",
        f"a2a --profile {target} log \"first day entry\" --status ok",
        f"a2a --profile {target} mail check",
    ]

    util.ok(
        {
            "profile": target,
            "default_profile": target,
            "created": created_dirs,
            "created_dirs": created_dirs,
            "changed": changed,
            "no_change": not changed,
            "idempotent": True,
            "directories": created_dirs if False else sorted(str(p) for p in [
                home / "mail" / target,
                home / "thoughts" / target,
            ]),
            "config": [str(p) for p in config_candidates],
            "default_profile_set": True,
            "doctor": doctor_data,
            "doctor_status": doctor_status,
            "status": doctor_status,
            "next_commands": next_commands,
            "next": next_commands,
            "commands": next_commands,
            "first_day_commands": next_commands,
        },
        elapsed_ms=int((time.time() - start) * 1000),
    )

def _dnd_check(args: Namespace, profile: str, start: float) -> None:
    path = util.HOME / "status" / f"{profile}.md"
    active = False
    until = None
    if path.exists():
        rec = util.read_record(path)
        if rec and rec.get("state") == "dnd":
            try:
                until_dt = util.parse_utc(rec["until"]) if rec.get("until") else None
            except Exception:
                until_dt = None
            if until_dt and until_dt > util.now_utc():
                active = True
                until = rec["until"]
            elif not until_dt:
                active = True
    util.ok({"active": active, "until": until}, elapsed_ms=int((time.time() - start) * 1000))
