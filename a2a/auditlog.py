"""Audit logging for a2a access events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import scrubber, util


def _scrub_value(value: Any) -> Any:
    """v0.6.0 §1: канал audit.details — любое секретное значение редактируется
    до записи (аудит-лог не должен становиться каналом утечки)."""
    if isinstance(value, str):
        clean, _ = scrubber.redact(value)
        return clean
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    return value


def log(event: str, caller: str, target: str, details: dict[str, Any] | None = None) -> None:
    entry = {
        "ts": util.utc_iso(),
        "event": event,
        "caller": caller,
        "target": scrubber.redact(str(target))[0],
        "details": _scrub_value(details or {}),
    }
    path = util.HOME / ".state" / "audit.log"
    util.ensure_dir(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def read_logs() -> list[dict[str, Any]]:
    path = util.HOME / ".state" / "audit.log"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]
