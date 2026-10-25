"""User feed projection rules + redaction (v0.7.0 §1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import util

DEFAULT_FEED_RULES: list[dict[str, str]] = [
    {"pattern": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "action": "redact"},
    {"pattern": r"\b[0-9a-fA-F]{32,}\b", "action": "redact"},
    {"pattern": r"\+?\d[\d\s().-]{8,}\d", "action": "redact"},
]

DEFAULT_RULES = DEFAULT_FEED_RULES


def _home(home: Any = None) -> Path:
    if home is None:
        return util.HOME
    return Path(home)


def rules_path(home: Any = None) -> Path:
    return _home(home) / ".state" / "feed_rules.json"


def _normalize_rule(item: Any) -> dict[str, str] | None:
    if item is None:
        return None
    if isinstance(item, str):
        return {"pattern": item, "action": "redact"}
    if isinstance(item, (list, tuple)):
        if not item:
            return None
        pat = item[0]
        action = item[1] if len(item) > 1 else "redact"
        return {"pattern": str(pat), "action": str(action).lower()}
    if isinstance(item, dict):
        pat = None
        for key in ("pattern", "regex", "re", "rule", "expr", "regexp"):
            if key in item and item[key] is not None:
                pat = item[key]
                break
        if pat is None:
            if len(item) == 1:
                k, v = next(iter(item.items()))
                if isinstance(v, str) and v.lower() in ("redact", "drop"):
                    return {"pattern": str(k), "action": str(v).lower()}
            return None
        action = None
        for key in ("action", "mode", "verb", "op", "type"):
            if key in item and item[key] is not None:
                action = item[key]
                break
        if action is None:
            action = "redact"
        return {"pattern": str(pat), "action": str(action).lower()}
    return None


def _normalize_rules(data: Any) -> list[dict[str, str]] | None:
    if data is None:
        return None
    if isinstance(data, dict):
        for key in ("rules", "patterns", "feed_rules"):
            if key in data:
                data = data[key]
                break
        else:
            if all(isinstance(v, str) and v.lower() in ("redact", "drop") for v in data.values()):
                return [{"pattern": str(k), "action": str(v).lower()} for k, v in data.items()]
            return None
    if not isinstance(data, list):
        return None
    out = []
    for item in data:
        r = _normalize_rule(item)
        if r is not None:
            out.append(r)
    return out


def load_rules(home: Any = None) -> list[dict[str, str]]:
    p = rules_path(home)
    if not p.exists():
        return [dict(r) for r in DEFAULT_FEED_RULES]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return [dict(r) for r in DEFAULT_FEED_RULES]
    rules = _normalize_rules(data)
    if rules is None:
        return [dict(r) for r in DEFAULT_FEED_RULES]
    return rules


def get_rules(home: Any = None) -> list[dict[str, str]]:
    return load_rules(home)


def default_rules() -> list[dict[str, str]]:
    return [dict(r) for r in DEFAULT_FEED_RULES]


def save_rules(rules: Any, home: Any = None) -> Path:
    p = rules_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    norm = _normalize_rules(rules)
    if norm is None:
        norm = []
        if isinstance(rules, list):
            for r in rules:
                if isinstance(r, dict):
                    norm.append({"pattern": str(r.get("pattern", r.get("regex", ""))),
                                 "action": str(r.get("action", "redact")).lower()})
                else:
                    norm.append({"pattern": str(r), "action": "redact"})
    final = []
    for r in norm:
        if isinstance(r, dict):
            final.append({"pattern": str(r.get("pattern", r.get("regex", ""))),
                         "action": str(r.get("action", "redact")).lower()})
        else:
            final.append({"pattern": str(r), "action": "redact"})
    p.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def set_rules_from_file(path: Any, home: Any = None) -> list[dict[str, str]]:
    src = Path(path)
    data = json.loads(src.read_text(encoding="utf-8"))
    rules = _normalize_rules(data)
    if rules is None:
        raise ValueError("invalid feed rules file")
    save_rules(rules, home)
    return rules


def _compiled(rules: Any, home: Any = None) -> list[tuple[dict[str, str], re.Pattern]]:
    if rules is None:
        rules = load_rules(home)
    if isinstance(rules, (str, Path)):
        # treat as home? or file? If file exists with rules, load from it
        try:
            if Path(rules).exists():
                # could be home dir or rules file; try rules file
                if Path(rules).name == "feed_rules.json" or Path(rules).suffix == ".json":
                    data = json.loads(Path(rules).read_text(encoding="utf-8"))
                    nr = _normalize_rules(data)
                    if nr is not None:
                        rules = nr
                else:
                    rules = load_rules(rules)
            else:
                rules = load_rules(rules)
        except Exception:
            rules = load_rules(home)
    norm = _normalize_rules(rules)
    if norm is None:
        if isinstance(rules, list):
            norm = []
            for r in rules:
                nr = _normalize_rule(r)
                if nr:
                    norm.append(nr)
        else:
            norm = load_rules(home)
    out = []
    for r in norm:
        try:
            pat = re.compile(r["pattern"])
        except re.error:
            continue
        out.append((r, pat))
    return out


def apply_rules(text: str, rules: Any = None, home: Any = None, **kwargs) -> str:
    if "home_dir" in kwargs:
        home = kwargs.pop("home_dir")
    if text is None:
        text = ""
    # allow rules passed as file path / home via kwargs
    if kwargs.get("path") is not None and rules is None:
        rules = kwargs.get("path")
    compiled = _compiled(rules, home)
    result = str(text)
    for r, pat in compiled:
        action = (r.get("action") or "redact").lower()
        if action == "drop":
            result = "\n".join(ln for ln in result.split("\n") if not pat.search(ln))
        else:
            result = pat.sub("[REDACTED]", result)
    return result


mechanical_pass = apply_rules
mechanical = apply_rules
redact = apply_rules
redact_text = apply_rules
redact_feed = apply_rules
project = apply_rules
project_text = apply_rules
feed_redact = apply_rules
transform = apply_rules
apply = apply_rules


def lint(text: str) -> tuple[bool, list[str]]:
    from . import scrubber

    return scrubber.check(text)


def scan(text: str) -> list[str]:
    from . import scrubber

    return scrubber.scan(text)


def load_feed_rules(home=None):
    return load_rules(home)


def get_feed_rules(home=None):
    return load_rules(home)


def read_rules(home=None):
    return load_rules(home)


def default_feed_rules():
    return default_rules()


# class-style compatibility
class Rules:
    @staticmethod
    def load(path=None, home=None):
        if path is not None and Path(path).exists() and Path(path).suffix == ".json":
            return set_rules_from_file(path, home)
        if path is not None:
            try:
                return load_rules(path)
            except Exception:
                pass
        return load_rules(home)

    @staticmethod
    def show(home=None):
        return load_rules(home)

    @staticmethod
    def apply(text, rules=None, home=None):
        return apply_rules(text, rules, home)


__all__ = [
    "DEFAULT_FEED_RULES",
    "DEFAULT_RULES",
    "apply_rules",
    "load_rules",
    "save_rules",
    "set_rules_from_file",
    "rules_path",
    "lint",
    "scan",
]