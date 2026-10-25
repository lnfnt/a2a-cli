"""Schema validation and defaults for a2a records."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import util

BASE_REQUIRED = {"id", "type", "author", "access", "created_at"}

VALID_TYPES = {
    "journal",
    "mail",
    "status",
    "thought",
    "lock",
    "alert",
    "handoff",
    "user-note",
    "task",
}

ACCESS_LEVELS = {"L1", "L2", "L3"}

DEFAULT_ACCESS = {
    "thought": "L1",
    "journal": "L2",
    "mail": "L2",
    "handoff": "L2",
    "lock": "L2",
    "alert": "L2",
    "status": "L2",
    "user-note": "L3",
    "task": "L2",
}

# v0.5.0 task state machine vocabulary (a2a/tasks.py owns transitions)
VALID_TASK_STATUSES = {
    "backlog", "assigned", "in_progress", "review",
    "done", "rejected", "blocked", "cancelled",
}
VALID_TASK_PRIORITIES = {"fyi", "today", "critical"}

VALID_STATES = {"busy", "idle", "blocked", "rest_mode", "low_battery", "dnd"}
VALID_MAIL_STATUS = {"unread", "read", "answered", "resolved"}
VALID_PRIORITY = {"today", "fyi", "critical"}
VALID_SEVERITY = {"info", "warn", "critical"}
VALID_HANDOFF_STATUS = {"pending", "in_progress", "done", "blocked"}
VALID_USER_KIND = {"decision-needed", "weekly", "fyi"}


def apply_defaults(meta: dict[str, Any], type_: str) -> None:
    if not meta.get("access"):
        meta["access"] = DEFAULT_ACCESS.get(type_, "L1")
    if not meta.get("id"):
        meta["id"] = util.new_id()
    if not meta.get("created_at"):
        meta["created_at"] = util.utc_iso()
    if not meta.get("record_version"):
        meta["record_version"] = "1.0"


def validate(meta: dict[str, Any], body: str = "") -> tuple[bool, str | None]:
    missing = [k for k in BASE_REQUIRED if not meta.get(k)]
    if missing:
        return False, f"missing required fields: {','.join(missing)}"
    type_ = meta.get("type")
    if type_ not in VALID_TYPES:
        return False, f"invalid type: {type_}"
    if meta.get("access") not in ACCESS_LEVELS:
        return False, f"invalid access: {meta.get('access')}"
    try:
        util.parse_utc(meta["created_at"])
    except Exception:
        return False, "invalid created_at"

    if type_ == "journal":
        if not meta.get("what_done"):
            return False, "journal requires what_done"
        if str(meta.get("status", "")).strip().lower() == "done" and not meta.get("artifacts"):  # v0.4.9 sa-0
            return False, "done journal requires artifacts"
        if meta.get("artifacts") and not isinstance(meta["artifacts"], list):
            return False, "artifacts must be a list"

    if type_ == "mail":
        if not meta.get("to"):
            return False, "mail requires to"
        if not meta.get("subject"):
            return False, "mail requires subject"
        if meta.get("msg_status") and meta["msg_status"] not in VALID_MAIL_STATUS:
            return False, "invalid msg_status"
        if meta.get("priority") and meta["priority"] not in VALID_PRIORITY:
            return False, "invalid priority"
        if meta.get("wake") is not None and not isinstance(meta["wake"], bool):
            return False, "wake must be bool"
        if meta.get("depth") is not None and not isinstance(meta["depth"], int):
            return False, "depth must be int"

    if type_ == "status":
        if meta.get("state") and meta["state"] not in VALID_STATES:
            return False, "invalid state"

    if type_ == "thought":
        if not body.strip():
            return False, "thought body is empty"

    if type_ == "lock":
        if not meta.get("target"):
            return False, "lock requires target"
        if not meta.get("expires"):
            return False, "lock requires expires"
        try:
            util.parse_utc(meta["expires"])
        except Exception:
            return False, "invalid expires"

    if type_ == "alert":
        if not meta.get("target"):
            return False, "alert requires target"
        if not meta.get("severity"):
            return False, "alert requires severity"
        if meta["severity"] not in VALID_SEVERITY:
            return False, "invalid severity"
        if not meta.get("active_until"):
            return False, "alert requires active_until"
        try:
            util.parse_utc(meta["active_until"])
        except Exception:
            return False, "invalid active_until"

    if type_ == "handoff":
        if not meta.get("to"):
            return False, "handoff requires to"
        if not meta.get("task"):
            return False, "handoff requires task"
        if not meta.get("acceptance"):
            return False, "handoff requires acceptance"
        if meta.get("h_status") and meta["h_status"] not in VALID_HANDOFF_STATUS:
            return False, "invalid h_status"

    if type_ == "user-note":
        if not body.strip():
            return False, "user-note body is empty"
        if meta.get("kind") and meta["kind"] not in VALID_USER_KIND:
            return False, "invalid kind"

    if type_ == "task":  # v0.5.0
        if not meta.get("title"):
            return False, "task requires title"
        if meta.get("status") not in VALID_TASK_STATUSES:
            return False, "invalid task status"
        if meta.get("priority") and meta["priority"] not in VALID_TASK_PRIORITIES:
            return False, "invalid task priority"
        if meta.get("deadline"):
            try:
                util.parse_utc(str(meta["deadline"]))
            except Exception:
                return False, "invalid task deadline"
        if meta.get("version") is not None and not isinstance(meta.get("version"), int):
            return False, "task version must be int"
        if meta.get("acceptance") is not None and not isinstance(meta["acceptance"], list):
            return False, "task acceptance must be a list"
        links = meta.get("links")
        if links is not None and not isinstance(links, dict):
            return False, "task links must be a mapping"
        hist = meta.get("assignee_history")
        if hist is not None and not isinstance(hist, list):
            return False, "task assignee_history must be a list"

    return True, None


def journal_status(meta: dict[str, Any]) -> str:
    if meta.get("status") == "UNVERIFIED_CLAIM":
        return "UNVERIFIED_CLAIM"
    artifacts = meta.get("artifacts") or []
    if artifacts:
        for a in artifacts:
            if not Path(a).exists():
                return "UNVERIFIED_CLAIM"
    return meta.get("status") or "ok"


def validate_rpc_request(req: dict[str, Any]) -> tuple[bool, str | None]:
    required = {"id", "from", "to", "question", "timeout", "created_at"}
    missing = required - set(req.keys())
    if missing:
        return False, f"missing rpc request fields: {','.join(sorted(missing))}"
    if not isinstance(req["timeout"], int) or req["timeout"] <= 0:
        return False, "invalid timeout"
    if not req.get("question"):
        return False, "question is empty"
    try:
        util.parse_utc(req["created_at"])
    except Exception:
        return False, "invalid created_at"
    return True, None


def validate_rpc_response(resp: dict[str, Any]) -> tuple[bool, str | None]:
    required = {"call_id", "from", "answer"}
    missing = required - set(resp.keys())
    if missing:
        return False, f"missing rpc response fields: {','.join(sorted(missing))}"
    if not resp.get("answer"):
        return False, "answer is empty"
    return True, None


RECORD_VERSION_CEILING = "1.0"
