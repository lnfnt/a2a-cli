"""v0.8.0 §6 (F-03/J-08): настраиваемые маски — per-канал × категория, режим redact|block.

Файл .state/scrub_config.json (нет файла / невалидный = полностью дефолтное поведение):
  {"mail": {"kv_secret": "block"}, "journal": {"phone": "block"}}
Категории: имена паттернов scrubber (kv_secret/yaml_secret/hex_key_32/phone/email/...
см. PATTERNS/NEW_PATTERNS). Значение только "redact" (дефолт) или "block".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import json

from . import util

VALID_MODES = {"redact", "block"}


def config_path() -> Path:
    return util.HOME / ".state" / "scrub_config.json"


def load() -> dict[str, dict[str, str]]:
    p = config_path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for chan, rules in raw.items():
        if not isinstance(rules, dict):
            continue
        clean = {}
        for cat, mode in rules.items():
            if str(mode) in VALID_MODES:
                clean[str(cat)] = str(mode)
        out[str(chan)] = clean
    return out


def mode_for(channel: str, category: str) -> str:
    return load().get(channel, {}).get(category, "redact")


def show() -> dict[str, Any]:
    return {"config": load(), "path": str(config_path()),
            "is_default": not config_path().exists()}


def set_from_file(path_str: str) -> dict[str, Any]:
    p = Path(path_str)
    if not p.exists():
        util.fail(util.EXIT_USAGE_ERROR, "config_not_found", {"path": path_str})
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        util.fail(util.EXIT_USAGE_ERROR, "config_invalid_json", {"error": str(e)[:120]})
        return {}  # unreachable: util.fail exits; placates type-checker
    if not isinstance(raw, dict):
        util.fail(util.EXIT_USAGE_ERROR, "config_invalid_shape", {})
    # normalize + validate
    norm: dict[str, dict[str, str]] = {}
    for chan, rules in raw.items():
        if not isinstance(rules, dict):
            util.fail(util.EXIT_USAGE_ERROR, "config_invalid_shape", {"channel": str(chan)})
        clean = {}
        for cat, mode in rules.items():
            if str(mode) not in VALID_MODES:
                util.fail(util.EXIT_USAGE_ERROR, "config_invalid_mode",
                          {"category": str(cat), "mode": str(mode),
                           "allowed": sorted(VALID_MODES)})
            clean[str(cat)] = str(mode)
        norm[str(chan)] = clean
    dest = config_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    util.write_atomically(dest, json.dumps(norm, ensure_ascii=False, indent=2))
    return {"rules": norm, "path": str(dest)}


def assert_not_blocked(channel: str, *texts: str) -> None:
    """Reject write if any hit category is configured mode=block for channel."""
    from . import scrubber
    rules = load().get(channel, {})
    blocked = {c for c, m in rules.items() if m == "block"}
    if not blocked:
        return
    hits = set(scrubber.scan("\n".join(t for t in texts if t)))
    tripped = sorted(hits & blocked)
    if tripped:
        util.fail(util.EXIT_ERROR, "scrub_blocked",
                  {"categories": tripped, "channel": channel,
                   "hint": "категории в режиме block — убери материал или смени "
                           "scrub-config --set (redact)",
                   "example": "a2a scrub-config --show"})
