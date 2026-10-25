"""Thought journal commands."""

from __future__ import annotations

import time
from argparse import Namespace
from pathlib import Path

from . import auditlog, schema, scrubber, util


def cmd_think(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    body = args.text
    if len(body) > 2000:
        util.fail(util.EXIT_INVALID_RECORD, "thought_too_long")
    access = args.access or schema.DEFAULT_ACCESS["thought"]
    if access not in schema.ACCESS_LEVELS:
        util.fail(util.EXIT_INVALID_RECORD, "invalid_access")

    topic = getattr(args, "topic", None)
    meta = {
        "type": "thought",
        "author": profile,
        "access": access,
        "topic": topic or "general",
    }
    schema.apply_defaults(meta, "thought")

    ok, reason = schema.validate(meta, body)
    if not ok:
        util.fail(util.EXIT_INVALID_RECORD, reason)

    text = util.join_front_matter(meta, body)
    dirty, hits = scrubber.check(text)
    if dirty:
        try:
            from . import quarantine as _q
            _q.quarantine_payload(profile, text, hits)
        except Exception:
            pass
        util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", 'a2a think "note without secrets"', {"hits": hits})

    created = util.parse_utc(meta["created_at"])
    path = util.record_path("thought", profile, access, created, meta["id"])
    util.write_atomically(path, text)
    util.ok({"id": meta["id"], "path": str(path), "access": access}, elapsed_ms=int((time.time() - start) * 1000))
