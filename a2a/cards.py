"""a2a.cards — P1-7: машинная проверка Agent Cards и gate вызовов.

Канон реализации валидации/правил — bin/validate_cards.py
один исходный текст; здесь только мост к каталогу cards() внутри A2A_HOME и
обёртка запрета с аудит-событием (fail-closed).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from . import util

_CANON = Path("bin/validate_cards.py")


def _impl():
    spec = importlib.util.spec_from_file_location("a2a_validate_cards_impl", _CANON)
    if spec is None or spec.loader is None:
        raise RuntimeError("validate_cards.py canon not loadable")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def cards_dir() -> Path:
    return util.resolve_home() / "cards"


def validate_all() -> tuple[bool, dict]:
    impl = _impl()
    return impl.validate_dir(cards_dir())


def who_can(task_token: str) -> list[dict]:
    impl = _impl()
    out = []
    for f in sorted(cards_dir().glob("*.json")):
        import json
        card = json.loads(f.read_text(encoding="utf-8"))
        caps = [c.lower() for c in card.get("capabilities", [])]
        if any(task_token.lower() in c for c in caps):
            out.append({"profile": card["name"], "capabilities": card["capabilities"],
                        "status": card["status"]})
    return out


def gate_call(caller: str, method: str, target: str, auditlog_mod) -> tuple[bool, str]:
    """Fail-closed проверка вызова против карточки получателя.

    Отказ пишется в audit log (event=card_denied) — как требует приёмка P1-7.
    """
    impl = _impl()
    allowed, reason = impl.can_call(caller, method, target, cards_dir())
    if not allowed:
        auditlog_mod.log("card_denied", caller, target, {"method": method, "reason": reason})
    return allowed, reason
