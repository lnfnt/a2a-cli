"""Digest command (v0.8.0 §2)."""

from __future__ import annotations

import time
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

from . import access as acl_mod, alerts, journal, schema, tasks as tasks_mod, util


def _collect_inbox_unread(profile: str) -> list[dict]:
    out = []
    base = util.HOME / "mail" / profile / "inbox"
    for path in util.list_files(base):
        rec = util.read_record(path)
        if not rec or rec.get("type") != "mail":
            continue
        if not acl_mod.may_read(rec, profile):
            continue
        if rec.get("msg_status") != "unread":
            continue
        out.append({
            "id": rec.get("id"),
            "author": rec.get("author"),
            "to": rec.get("to"),
            "subject": rec.get("subject"),
            "priority": rec.get("priority"),
            "created_at": rec.get("created_at"),
        })
    return out


def _collect_outbox_unanswered(profile: str) -> list[dict]:
    out = []
    base = util.HOME / "mail" / profile / "outbox"
    for path in util.list_files(base):
        rec = util.read_record(path)
        if not rec or rec.get("type") != "mail":
            continue
        if rec.get("msg_status") in ("answered", "resolved"):
            continue
        out.append({
            "id": rec.get("id"),
            "to": rec.get("to"),
            "subject": rec.get("subject"),
            "priority": rec.get("priority"),
            "created_at": rec.get("created_at"),
            "msg_status": rec.get("msg_status"),
        })
    return out


def _collect_active_tasks(profile: str) -> list[dict]:
    out = []
    terminal = tasks_mod.TERMINAL_STATUSES
    for rec in tasks_mod._iter_visible_tasks(profile):
        if str(rec.get("status")) in terminal:
            continue
        if rec.get("owner") != profile and rec.get("author") != profile:
            continue
        out.append({
            "id": rec.get("id"),
            "title": rec.get("title"),
            "status": rec.get("status"),
            "owner": rec.get("owner"),
            "priority": rec.get("priority"),
            "deadline": rec.get("deadline"),
            "updated_at": rec.get("updated_at"),
        })
    return out


def _collect_alerts(profile: str) -> list[dict]:
    now = util.now_utc()
    out = []
    for path in util.list_files(util.HOME / "alerts"):
        rec = util.read_record(path)
        if not rec or rec.get("type") != "alert":
            continue
        if not acl_mod.may_read(rec, profile):
            continue
        try:
            until = util.parse_utc(rec["active_until"])
        except Exception:
            continue
        if until <= now:
            continue
        target = str(rec.get("target") or "")
        if target not in (profile, "all", "all-lex", "@all-lex", "*") and rec.get("author") != profile:
            continue
        out.append({
            "id": rec.get("id"),
            "author": rec.get("author"),
            "target": target,
            "severity": rec.get("severity"),
            "active_until": rec.get("active_until"),
        })
    return out


def _send_self_mail(profile: str, summary: dict) -> str:
    now = util.now_utc()
    mid = util.new_id()
    meta = {
        "type": "mail",
        "id": mid,
        "author": profile,
        "to": profile,
        "access": "L2",
        "subject": "Digest",
        "msg_status": "unread",
        "priority": "fyi",
        "thread": mid,
        "created_at": util.utc_iso(now),
    }
    schema.apply_defaults(meta, "mail")
    body = "Digest summary"
    text = util.join_front_matter(meta, body)
    created = util.parse_utc(meta["created_at"])
    out_path = util.HOME / "mail" / profile / "outbox" / f"{created.strftime('%Y-%m-%d')}_{mid}.md"
    in_path = util.HOME / "mail" / profile / "inbox" / f"{created.strftime('%Y-%m-%d')}_{mid}.md"
    util.commit_bundle([(out_path, text), (in_path, text)], label=f"mail:{mid}")
    return mid


def cmd_digest(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    inbox = _collect_inbox_unread(profile)
    outbox = _collect_outbox_unanswered(profile)
    active_tasks = _collect_active_tasks(profile)
    active_alerts = _collect_alerts(profile)

    data = {
        "profile": profile,
        "inbox_unread": inbox,
        "outbox_unanswered": outbox,
        "tasks": active_tasks,
        "alerts": active_alerts,
        # aliases
        "inbox": inbox,
        "unread": inbox,
        "unread_inbox": inbox,
        "outbox": outbox,
        "unanswered": outbox,
        "unanswered_outbox": outbox,
        "active_tasks": active_tasks,
        "tasks_active": active_tasks,
        "active_alerts": active_alerts,
        "count": len(inbox) + len(outbox) + len(active_tasks) + len(active_alerts),
    }

    if getattr(args, "mail", False):
        mid = _send_self_mail(profile, data)
        data["mail_id"] = mid
        data["mail_created"] = mid
        data["mailed"] = True

    util.ok(data, elapsed_ms=int((time.time() - start) * 1000))
