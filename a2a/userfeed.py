"""User feed / publication board commands."""

from __future__ import annotations

import os
import time
from argparse import Namespace
from pathlib import Path

from . import feed_rules as user, schema, scrubber, util

# expose user helpers under userfeed for compatibility
apply_feed_rules = user.apply_rules
load_feed_rules = user.load_rules
get_feed_rules = user.get_rules
DEFAULT_FEED_RULES = user.DEFAULT_FEED_RULES
DEFAULT_RULES = user.DEFAULT_RULES
rules_path = user.rules_path

# Note: односторонняя публикация в NotesApp user-feed (User читает с телефона).
# Canonical остаётся в shared_workspace/user-feed/; сюда кладётся копия с BOM.
USER_FEED = Path(
    os.environ.get("A2A_USER_FEED") or str(Path(os.environ.get("A2A_HOME") or (Path.home() / ".a2a")) / "user-feed")
)


def mirror_to_feed(path: Path, text: str) -> Path | None:
    """Copy a user-note into the NotesApp vault (best-effort, non-fatal)."""
    try:
        USER_FEED.mkdir(parents=True, exist_ok=True)
        dest = USER_FEED / path.name
        data = text.encode("utf-8")
        if not data.startswith(b"\xef\xbb\xbf"):
            data = b"\xef\xbb\xbf" + data  # project convention: BOM for phone NotesApp
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)  # atomic
        return dest
    except Exception:
        return None  # публикация не должна падать из-за зеркала; doctor покажет


def cmd_user_post(args: Namespace) -> None:
    start = time.time()
    from . import scrub_config as _sc
    _sc.assert_not_blocked("user", args.text or "")
    profile = util.resolve_profile(args)
    response = write_note(profile, args.text, args.kind or "fyi", args.access)
    util.ok(response, elapsed_ms=int((time.time() - start) * 1000))


def cmd_user_rules(args: Namespace) -> None:
    start = time.time()
    if getattr(args, "set_file", None):
        rules = user.set_rules_from_file(args.set_file)
        util.ok({"count": len(rules), "rules": rules,
                "path": str(user.rules_path())},
               elapsed_ms=int((time.time() - start) * 1000))
        return
    # default: show
    rules = user.load_rules()
    util.ok({"rules": rules, "path": str(user.rules_path())},
           elapsed_ms=int((time.time() - start) * 1000))


def write_note(profile: str, text: str, kind: str = "fyi", access: str | None = None,
               defer: bool = False) -> dict:
    """Build, validate and write one user-note; returns the response dict.

    v0.5.0: extracted from cmd_user_post for task-terminal publication;
    behaviour identical to v0.4.10 `user post`. `defer=True` builds and
    validates but does NOT write the file — the caller commits it via
    commit_bundle and mirrors afterwards (path/text still returned).
    """
    body = text
    # v0.7.0 §1: mechanical redaction pass before publication (source untouched).
    body = user.apply_rules(body)

    access = access or schema.DEFAULT_ACCESS["user-note"]
    meta = {
        "type": "user-note",
        "author": profile,
        "access": access,
        "kind": kind,
    }
    schema.apply_defaults(meta, "user-note")

    ok, err = schema.validate(meta, body)
    if not ok:
        util.fail(util.EXIT_INVALID_RECORD, err)

    text = util.join_front_matter(meta, body)
    dirty, hits = scrubber.check(text)
    if dirty:
        try:
            from . import quarantine as _q
            _q.quarantine_payload(profile, text, hits)
        except Exception:
            pass
        util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", 'a2a user post "note without secrets"', {"hits": hits})

    created = util.parse_utc(meta["created_at"])
    path = util.record_path("user-note", profile, access, created, meta["id"])
    if not defer:
        util.write_atomically(path, text)
        mirrored = mirror_to_feed(path, text)
        return {"id": meta["id"], "path": str(path), "kind": kind,
                "notesapp": str(mirrored) if mirrored else None}
    return {"id": meta["id"], "path": str(path), "kind": kind,
            "text": text, "notesapp": None}
