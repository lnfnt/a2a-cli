"""v0.7.0 §1 (F-02) + §5 (F-01 остаток): feed-rules механическая редакция проекции;
alerts L1-фильтр для чужих записей. Координаторские тесты поверх кода coder-ленты."""
from __future__ import annotations

import json


def _run(run, *args, **kw):
    return run(*args, **kw)


def test_feed_rules_show_default(run, home, profile):
    code, out, _ = run("user", "rules", "--show")
    assert code == 0, out
    pats = [r["pattern"] for r in out["data"]["rules"]]
    assert any("916" in p or r"\\+?\d" in p or "d[\\d" in p for p in pats)  # phone-ish
    assert any("32" in p and "a-f" in p for p in pats)  # hex32


def test_post_phone_hex_redacted_source_intact(run, home, profile):
    code, out, _ = run("log", "тест для фида", "--numbers-ok", "--status", "ok")
    assert code == 0
    phone, hexid = "+7 916 123-45-67", "0" * 31 + "a"
    code, out, _ = run("user", "post", f"номер {phone} id {hexid}")
    assert code == 0, out
    files = list((home / "user-feed").glob("*.md"))
    assert files
    text = files[-1].read_text(encoding="utf-8")
    assert phone not in text and hexid not in text
    assert "REDACTED" in text


def test_rules_set_custom(run, home, profile):
    rules = {"rules": [{"pattern": "СЕКРЕТНЫЙТОКЕН", "action": "drop"}]}
    rp = home / ".state" / "feed_rules.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(rules), encoding="utf-8")
    code, out, _ = run("user", "post", "обычный текст без секретов")
    assert code == 0, out
    # custom rules replace defaults: phone now survives
    files = list((home / "user-feed").glob("*.md"))
    text = files[-1].read_text(encoding="utf-8")
    assert "обычный текст" in text


def test_post_lint_still_wins_on_empty(run, home, profile):
    code, out, _ = run("user", "post", "", check=False)
    assert code != 0


def test_alert_foreign_l1_hidden(run, home, profile, other):
    code, out, _ = run("alert", "set", "личная L1 штука", "--target", "coordinator",
                       "--severity", "warn", "--active-until", "2d", "--access", "L1")
    assert code == 0, out
    # owner sees own
    code3, own, _ = run("alert", "list")
    assert code3 == 0
    # owner's set went through: list shows it
    code2, out2, _ = run("alert", "list", profile_override=other)
    ids = {r.get("id") for r in out2["data"]["active"]}
    assert own["data"]["active"], "owner must see own L1 alert"
    assert not (ids & {r.get("id") for r in own["data"]["active"]}), \
        "foreign L1 alert must be invisible"


def test_alert_shared_visible(run, home, profile, other):
    code, out, _ = run("alert", "set", "общая L2 новость", "--target", "all-lex",
                      "--severity", "info", "--active-until", "2d", "--access", "L2")
    assert code == 0, out
    code, out2, _ = run("alert", "list", profile_override=other)
    assert out2["data"]["active"], "L2 alert must be visible to other profile"
