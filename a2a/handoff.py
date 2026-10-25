"""Handoff commands."""

from __future__ import annotations

import time
from argparse import Namespace

from . import schema, scrubber, util


def cmd_handoff(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    if args.list_for:
        return _handoff_list(args, start)

    to = args.to
    # v0.4.9 (независимый аудит sa-0, critical): адресат валидируется как имя
    # профиля — раньше ../../escape писал за корень шины (path traversal).
    if not util.validate_profile_name(to):
        util.fail(util.EXIT_USAGE_ERROR, "invalid_profile_name")
    if to == profile:
        util.fail(util.EXIT_USAGE_ERROR, "self_handoff_forbidden")
    task = args.task
    acceptance = args.acceptance
    payload = args.payload or None

    access = args.access or schema.DEFAULT_ACCESS["handoff"]
    meta = {
        "type": "handoff",
        "author": profile,
        "access": access,
        "to": to,
        "task": task,
        "acceptance": acceptance,
        "payload": payload,
        "h_status": "pending",
    }
    schema.apply_defaults(meta, "handoff")

    ok, err = schema.validate(meta, "")
    if not ok:
        util.fail(util.EXIT_INVALID_RECORD, err)

    body = f"Task: {task}\nAcceptance: {acceptance}\n"
    if payload:
        body += f"Payload: {payload}\n"

    text = util.join_front_matter(meta, body)
    dirty, hits = scrubber.check(text)
    if dirty:
        try:
            from . import quarantine as _q
            _q.quarantine_payload(profile, text, hits)
        except Exception:
            pass
        util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", "a2a handoff --to other --task t", {"hits": hits})

    created = util.parse_utc(meta["created_at"])
    path = util.record_path("handoff", profile, access, created, meta["id"])
    util.write_atomically(path, text)

    # register a reference mail in recipient inbox pointing to the canonical handoff file
    ref_meta = {
        "type": "mail",
        "author": profile,
        "to": to,
        "access": access,
        "subject": f"Handoff: {task}",
        "msg_status": "unread",
        "priority": "today",
        "ref": str(path),
        "ref_type": "handoff",
    }
    schema.apply_defaults(ref_meta, "mail")
    ok, err = schema.validate(ref_meta, "")
    if not ok:
        util.fail(util.EXIT_INVALID_RECORD, err)
    ref_body = f"Handoff reference: {path}\nTask: {task}\nAcceptance: {acceptance}\n"
    ref_text = util.join_front_matter(ref_meta, ref_body)
    ref_path = util.HOME / "mail" / to / "inbox" / path.name
    util.write_atomically(ref_path, ref_text)

    util.ok({"id": meta["id"], "to": to, "path": str(path), "inbox_ref": str(ref_path)}, elapsed_ms=int((time.time() - start) * 1000))


def _handoff_list(args: Namespace, start: float) -> None:
    profile = args.list_for
    entries = []
    for path in util.list_files(util.HOME / "records" / "handoffs"):
        rec = util.read_record(path)
        if not rec:
            continue
        if rec.get("to") != profile:
            continue
        if args.status and rec.get("h_status") != args.status:
            continue
        entries.append({
            "id": rec["id"],
            "author": rec.get("author"),
            "task": rec.get("task"),
            "h_status": rec.get("h_status"),
            "created_at": rec.get("created_at"),
        })
    util.ok({"count": len(entries), "entries": entries}, elapsed_ms=int((time.time() - start) * 1000))
