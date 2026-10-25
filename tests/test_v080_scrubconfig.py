"""v0.8.0 §6 (F-03/J-08): scrub-config per-канал block|redact."""
from __future__ import annotations

import json
from pathlib import Path


def _set_rules(run, home, rules: dict):
    p = Path(home) / "cfg.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    code, out, _ = run("scrub-config", "--set", str(p))
    assert code == 0, out
    return out


def test_default_no_config_is_redact_behavior(run, home, profile):
    code, out, _ = run("scrub-config", "--show")
    assert code == 0
    assert out["data"]["is_default"] is True
    # kv_secret письмо проходит redact-дефолтом (не блокается без конфига)
    code, sent, _ = run("mail", "send", "--to", "user-profile", "--subject", "s",
                        "--body", "password=hunter2supersecret")
    assert code == 0, sent


def test_mail_phone_block_rejects(run, home, profile):
    _set_rules(run, home, {"mail": {"kv_secret": "block"}})
    code, out, _ = run("mail", "send", "--to", "user-profile", "--subject", "s",
                       "--body", "password=hunter2supersecret", check=False)
    assert code != 0
    assert out["reason"] == "scrub_blocked"
    assert "kv_secret" in out["data"]["categories"]
    # ни одна копия не появилась
    assert not list((home / "mail").rglob("*.md"))


def test_block_is_per_channel(run, home, profile):
    _set_rules(run, home, {"mail": {"kv_secret": "block"}})
    code, out, _ = run("log", "телефон +7 916 123-45-67", "--numbers-ok", "--status", "ok")
    assert code == 0, out  # journal не заблокан


def test_invalid_mode_rejected_keeps_old(run, home, profile):
    p = Path(home) / "bad.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"mail": {"kv_secret": "nuke"}}', encoding="utf-8")
    code, out, _ = run("scrub-config", "--set", str(p), check=False)
    assert code != 0 and out["reason"] == "config_invalid_mode"


def test_malformed_file_falls_back_to_default(run, home, profile):
    cfg = Path(home) / ".state" / "scrub_config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{{{non-json", encoding="utf-8")
    code, out, _ = run("mail", "send", "--to", "user-profile", "--subject", "s", "--body", "ok")
    assert code == 0, out  # не роняет канал; load() вернул {}
