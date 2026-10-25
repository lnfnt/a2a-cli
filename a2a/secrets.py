"""Secrets lifecycle aggregation (v0.7.0 §3)."""
from __future__ import annotations

import json
import time
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from pathlib import Path
from argparse import Namespace

from . import auditlog, util


def _audit_events() -> list[dict]:
    try:
        return auditlog.read_logs()
    except Exception:
        return []


def _secret_events() -> list[dict]:
    return [e for e in _audit_events() if e.get("event") == "secret_redacted"]


def _week_key(ts: str) -> str:
    try:
        dt = util.parse_utc(ts)
    except Exception:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return ts[:7]
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _day_key(ts: str) -> str:
    try:
        dt = util.parse_utc(ts)
    except Exception:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return ts[:10]
    return dt.strftime("%Y-%m-%d")


def build_report(days: int | None = None, profile: str | None = None) -> dict:
    events = _secret_events()
    if days is not None:
        cutoff = util.now_utc() - timedelta(days=days)
        filtered = []
        for e in events:
            try:
                dt = util.parse_utc(e.get("ts", ""))
            except Exception:
                filtered.append(e)
                continue
            if dt >= cutoff:
                filtered.append(e)
        events = filtered

    if profile is not None:
        events = [e for e in events if (e.get("caller") or e.get("profile")) == profile]

    by_channel: Counter = Counter()
    by_week: Counter = Counter()
    by_profile: Counter = Counter()
    by_day_profile: dict[str, Counter] = defaultdict(Counter)
    for e in events:
        channel = e.get("target") or e.get("channel") or "unknown"
        by_channel[channel] += 1
        by_week[_week_key(e.get("ts", ""))] += 1
        caller = e.get("caller") or e.get("profile") or "unknown"
        by_profile[caller] += 1
        by_day_profile[_day_key(e.get("ts", ""))][caller] += 1

    # quarantine counter (v0.9.0 §6, G-07)
    try:
        from . import quarantine as _q
        qfiles = list((_q._qdir()).glob("*.md")) if _q._qdir().exists() else []
    except Exception:
        qfiles = []
    qcount = 0
    qevents = sum(1 for e in _audit_events() if e.get("event") == "secret_quarantined")
    for qp in qfiles:
        try:
            rec = util.read_record(qp) if False else None
        except Exception:
            rec = None
        # filter by profile if requested
        author = None
        try:
            import yaml
            text = qp.read_text(encoding="utf-8")
            # simple front-matter parse
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    meta = yaml.safe_load(parts[1]) or {}
                    author = meta.get("author")
        except Exception:
            author = None
        if profile is not None and author is not None and str(author) != str(profile):
            continue
        qcount += 1
    if profile is not None:
        # also filter audit events
        qevents = sum(1 for e in _audit_events()
                      if e.get("event") == "secret_quarantined"
                      and ((e.get("caller") or e.get("profile")) == profile))

    total = len(events)
    summary = {
        "total": total,
        "count": total,
        "events": total,
        "total_events": total,
        "quarantine": qcount,
        "quarantined": qcount,
        "quarantine_count": qcount,
        "quarantined_count": qcount,
        "quarantine_total": qcount,
        "quarantined_total": qcount,
        "quarantine_files": qcount,
        "quarantined_files": qcount,
        "quarantine_items": qcount,
        "quarantine_events": qevents,
        "quarantined_events": qevents,
        "by_channel": dict(by_channel),
        "channels": dict(by_channel),
        "channel_counts": dict(by_channel),
        "counts_by_channel": dict(by_channel),
        "per_channel": dict(by_channel),
        "by_week": dict(by_week),
        "weeks": dict(by_week),
        "weekly_counts": dict(by_week),
        "counts_by_week": dict(by_week),
        "by_profile": dict(by_profile),
        "profiles": dict(by_profile),
        "profile_counts": dict(by_profile),
        "by_day": {k: dict(v) for k, v in by_day_profile.items()},
        "daily": {k: dict(v) for k, v in by_day_profile.items()},
    }
    summary["report"] = {k: v for k, v in summary.items() if k != "report"}
    return summary


def cmd_secrets_report(args: Namespace) -> None:
    start = time.time()
    days = getattr(args, "days", None)
    profile = getattr(args, "profile", None)
    report = build_report(days=days, profile=profile)
    notified = False
    if getattr(args, "notify", False):
        flag = util.HOME / ".state" / "secrets_watch"
        util.ensure_dir(flag)
        flag.write_text(json.dumps({"flag": True, "count": report["total"],
                                   "notified_at": util.utc_iso(), "by_channel": report["by_channel"]},
                                  ensure_ascii=False, sort_keys=True) + "\n",
                       encoding="utf-8")
        notified = True
    report["notified"] = notified
    util.ok(report, elapsed_ms=int((time.time() - start) * 1000))
