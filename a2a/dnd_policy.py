"""DND whitelist policy (v0.9.0 §11, E-03)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import util


def path() -> Path:
    if os.environ.get("A2A_HOME"):
        return util.resolve_home() / ".state" / "dnd_policy.json"
    return util.HOME / ".state" / "dnd_policy.json"


def read_policy():
    """Return (policy_list, warning, missing)."""
    p = path()
    if not p.exists():
        return [], None, True
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return [], "invalid_json", False
    if not isinstance(data, list):
        return [], "invalid_json", False
    return data, None, False


def matches(kind: str, **attrs) -> bool:
    policy, warning, missing = read_policy()
    if warning:
        return False
    # fyi never wakes, even if listed in policy.
    if attrs.get("priority") == "fyi" or attrs.get("severity") == "fyi":
        return False
    for rule in policy:
        if not isinstance(rule, dict):
            continue
        if rule.get("kind") != kind:
            continue
        ok = True
        for key, val in attrs.items():
            if key in rule and rule.get(key) != val:
                ok = False
                break
        if ok:
            return True
    return False
