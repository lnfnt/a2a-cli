"""Metrics aggregation (v0.9.0 §9, H-04)."""

from __future__ import annotations

import json
import time
from argparse import Namespace
from datetime import timedelta

from . import auditlog, util


def _cutoff(weeks):
    if weeks is None:
        return None
    try:
        n = int(weeks)
    except Exception:
        return None
    if n <= 0:
        return None
    return util.now_utc() - timedelta(weeks=n)


def _ts_ok(ts, cutoff):
    if cutoff is None:
        return True
    try:
        return util.parse_utc(ts) >= cutoff
    except Exception:
        return True


def cmd_metrics(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    weeks = getattr(args, "week", None)
    cutoff = _cutoff(weeks)

    mail_count = 0
    wake_frontmatter = 0
    for path in util.list_files(util.HOME / "mail" / profile):
        rec = util.read_record(path)
        if not rec or rec.get("type") != "mail":
            continue
        if not _ts_ok(rec.get("created_at"), cutoff):
            continue
        mail_count += 1
        if rec.get("wake"):
            wake_frontmatter += 1

    events = auditlog.read_logs()
    wake_events = 0
    scrub_events = 0
    task_transitions = 0
    for e in events:
        if not _ts_ok(e.get("ts"), cutoff):
            continue
        caller = e.get("caller") or e.get("profile")
        if caller is not None and str(caller) != str(profile):
            continue
        ev = str(e.get("event") or "")
        low = ev.lower()
        if "wake" in low:
            wake_events += 1
        if low in ("secret_redacted", "secret_detected", "secret_quarantined") or "scrub" in low or "secret" in low:
            scrub_events += 1
        if low.startswith("task_"):
            task_transitions += 1

    wake_count = wake_events + wake_frontmatter

    data = {
        "week": weeks,
        "weeks": weeks,
        "period_weeks": weeks,
        "mail": mail_count,
        "mails": mail_count,
        "mail_count": mail_count,
        "emails": mail_count,
        "email_count": mail_count,
        "wake": wake_count,
        "wake_count": wake_count,
        "wakes": wake_count,
        "wake_events": wake_events,
        "wake_frontmatter": wake_frontmatter,
        "scrub": scrub_events,
        "scrub_events": scrub_events,
        "scrub_count": scrub_events,
        "scrubbed": scrub_events,
        "secret_events": scrub_events,
        "task_transitions": task_transitions,
        "task_transition_count": task_transitions,
        "task_transitions_count": task_transitions,
        "transitions": task_transitions,
        "counts": {
            "mail": mail_count,
            "wake": wake_count,
            "scrub": scrub_events,
            "task_transitions": task_transitions,
        },
    }
    util.ok(data, elapsed_ms=int((time.time() - start) * 1000))
