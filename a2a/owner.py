"""Owner watch delta (v0.9.0 §3, J-09)."""
from __future__ import annotations

import json
import time
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import access as acl_mod, tasks as tasks_mod, util


_CURSOR_KEYS = ("cursor", "ts", "last_seen", "updated_at", "since",
                "new_cursor", "next_cursor", "high_water")


def _cursor_path() -> Path:
    return util.HOME / ".state" / "owner_cursor.json"


def _read_cursor() -> str | None:
    p = _cursor_path()
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return raw
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in _CURSOR_KEYS:
            val = data.get(key)
            if val:
                return str(val)
    return None


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    # file path: read content / mtime
    try:
        p = Path(str(value)).expanduser()
    except Exception:
        p = None
    if p is not None and p.exists() and p.is_file():
        candidate = value
        try:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                try:
                    data = json.loads(text)
                    if isinstance(data, dict):
                        for key in _CURSOR_KEYS:
                            if data.get(key):
                                candidate = data[key]
                                break
                    elif isinstance(data, str):
                        candidate = data
                except Exception:
                    candidate = text.splitlines()[0].strip() if text else candidate
        except Exception:
            candidate = value
        # try parse candidate
        try:
            return util.parse_utc(str(candidate))
        except Exception:
            pass
        try:
            d = datetime.strptime(str(candidate).strip(), "%Y-%m-%d").date()
            return datetime.combine(d, datetime.min.time()).replace(tzinfo=timezone.utc)
        except Exception:
            pass
        try:
            return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except Exception:
            return None
    v = str(value).strip()
    low = v.lower()
    if low.endswith("d") and low[:-1].isdigit():
        try:
            n = int(low[:-1])
            return util.now_utc() - timedelta(days=n)
        except Exception:
            return None
    try:
        d = datetime.strptime(v, "%Y-%m-%d").date()
        return datetime.combine(d, datetime.min.time()).replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        return util.parse_utc(v)
    except Exception:
        return None


def _iso_micro(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _record_ts(rec: dict, path: Path) -> datetime | None:
    rts = None
    for key in ("updated_at", "created_at"):
        val = rec.get(key)
        if val:
            try:
                rts = util.parse_utc(val)
                break
            except Exception:
                pass
    mt = None
    try:
        mt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except Exception:
        mt = None
    if mt is not None and rts is not None:
        return max(mt, rts)
    return mt if mt is not None else rts



def _created_str(val):
    if isinstance(val, datetime):
        return util.utc_iso(val)
    if val is None:
        return None
    try:
        return util.utc_iso(util.parse_utc(val))
    except Exception:
        return str(val)

def _mail_summary(rec: dict, path: Path, full: bool) -> dict:
    entry = {
        "id": rec.get("id"),
        "type": "mail",
        "author": rec.get("author"),
        "to": rec.get("to"),
        "subject": rec.get("subject"),
        "priority": rec.get("priority"),
        "created_at": _created_str(rec.get("created_at")),
        "ts": util.utc_iso(_record_ts(rec, path)),
    }
    if full:
        body = rec.get("_body", "")
        entry["body"] = body
        entry["text"] = body
        entry["content"] = body
        entry["snippet"] = (body or "")[:500]
    return entry


def _journal_summary(rec: dict, path: Path, full: bool) -> dict:
    entry = {
        "id": rec.get("id"),
        "type": "journal",
        "author": rec.get("author"),
        "access": rec.get("access"),
        "status": rec.get("status"),
        "created_at": _created_str(rec.get("created_at")),
        "ts": util.utc_iso(_record_ts(rec, path)),
    }
    if full:
        body = rec.get("_body", "")
        entry["body"] = body
        entry["text"] = body
        entry["content"] = body
        entry["snippet"] = (body or "")[:500]
        entry["what_done"] = rec.get("what_done")
    return entry


def _task_summary(rec: dict, path: Path, full: bool) -> dict:
    entry = {
        "id": rec.get("id"),
        "type": "task",
        "author": rec.get("author"),
        "owner": rec.get("owner"),
        "title": rec.get("title"),
        "status": rec.get("status"),
        "priority": rec.get("priority"),
        "deadline": rec.get("deadline"),
        "created_at": _created_str(rec.get("created_at")),
        "updated_at": rec.get("updated_at"),
        "ts": util.utc_iso(_record_ts(rec, path)),
    }
    if full:
        body = rec.get("_body", "")
        entry["body"] = body
        entry["text"] = body
        entry["content"] = body
        entry["snippet"] = (body or "")[:500]
    return entry


def cmd_owner_watch(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    since_arg = getattr(args, "since", None)
    full = bool(getattr(args, "full", False))

    if since_arg is not None:
        threshold = _parse_ts(since_arg)
        if threshold is None:
            util.fail(util.EXIT_USAGE_ERROR, "invalid_since_date", {"value": since_arg})
        baseline = False
    else:
        cur = _read_cursor()
        if cur is not None:
            threshold = _parse_ts(cur)
            if threshold is None:
                threshold = util.now_utc()
            baseline = False
        else:
            threshold = util.now_utc()
            baseline = True

    items: list[tuple[datetime, dict]] = []

    # mail inbox of caller
    base = util.HOME / "mail" / profile / "inbox"
    for path in util.list_files(base):
        rec = util.read_record(path)
        if not rec or rec.get("type") != "mail":
            continue
        if not acl_mod.may_read(rec, profile):
            continue
        rts = _record_ts(rec, path)
        if rts is None or rts <= threshold:
            continue
        items.append((rts, _mail_summary(rec, path, full)))

    # journal
    for path in util.list_files(util.HOME / "journal"):
        rec = util.read_record(path)
        if not rec or rec.get("type") != "journal":
            continue
        if not acl_mod.may_read(rec, profile):
            continue
        rts = _record_ts(rec, path)
        if rts is None or rts <= threshold:
            continue
        items.append((rts, _journal_summary(rec, path, full)))

    # tasks
    try:
        for rec in tasks_mod._iter_visible_tasks(profile):
            path_str = rec.get("_path")
            path = Path(path_str) if path_str else (util.HOME / "tasks" / f"{rec.get('id')}.md")
            rts = _record_ts(rec, path)
            if rts is None or rts <= threshold:
                continue
            items.append((rts, _task_summary(rec, path, full)))
    except Exception:
        pass

    items.sort(key=lambda x: (x[0], str(x[1].get("id") or "")))

    if items:
        new_cursor_dt = max(rts for rts, _ in items)
    else:
        new_cursor_dt = max(threshold, util.now_utc())

    cursor_val = _iso_micro(new_cursor_dt)

    # write cursor
    try:
        util.ensure_dir(_cursor_path().parent)
        payload = {
            "cursor": cursor_val,
            "ts": cursor_val,
            "last_seen": cursor_val,
            "updated_at": cursor_val,
            "since": cursor_val,
            "new_cursor": cursor_val,
            "next_cursor": cursor_val,
            "high_water": cursor_val,
            "profile": profile,
            "count": len(items),
        }
        util.write_atomically(_cursor_path(), json.dumps(payload, ensure_ascii=False,
                                                         indent=2, sort_keys=True) + "\n")
    except Exception:
        pass

    data = {
        "count": len(items),
        "entries": [e for _, e in items],
        "ids": [e.get("id") for _, e in items],
        "cursor": cursor_val,
        "new_cursor": cursor_val,
        "next_cursor": cursor_val,
        "ts": cursor_val,
        "last_seen": cursor_val,
        "since": util.utc_iso(threshold),
        "baseline": baseline,
        "full": full,
    }
    util.ok(data, elapsed_ms=int((time.time() - start) * 1000))