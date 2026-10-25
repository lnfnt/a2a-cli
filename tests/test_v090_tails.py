"""v0.9.0 хвосты, координационные пины: dedup, quarantine, retention, metrics, export, dnd-policy."""
from __future__ import annotations

import json
from pathlib import Path


def test_dedup_window(run, home, profile, other):
    code, o1, _ = run("mail", "send", "--to", other, "--subject", "s", "--body", "b",
                      "--idempotency-key", "KK1")
    assert code == 0
    code, o2, _ = run("mail", "send", "--to", other, "--subject", "s", "--body", "b",
                      "--idempotency-key", "KK1", check=False)
    # J-10: повтор в окне = идемпотентный реплей первого, не ошибка и не дубль
    assert code == 0, o2
    assert o2["data"]["id"] == o1["data"]["id"]
    assert (o2["data"].get("duplicate") or o2["data"].get("dedup")
            or str(o2.get("status", "")).startswith("duplicate")
            or o2["data"].get("replayed")), o2


def test_quarantine_on_hard_secret(run, home, profile, other):
    pem = "-----BEGIN RSA PRIVATE KEY-----\nQUJD\n-----END RSA PRIVATE KEY-----"
    code, out, _ = run("mail", "send", "--to", other, "--subject", "s", "--body", pem,
                       check=False)
    assert code != 0 and out["reason"] == "secret_detected"
    q = list((Path(home) / ".state" / "quarantine").glob("*.md"))
    assert q, "quarantine file expected"
    code, lst, _ = run("quarantine", "list")
    assert code == 0 and lst["data"]["count"] >= 1


def test_prune_dry_default_and_unread_immune(run, home, profile, other):
    code, out, _ = run("mail", "send", "--to", other, "--subject", "непрочитано",
                       "--body", "б")
    assert code == 0
    code, pr, _ = run("prune", "--older-than", "0d")
    assert code == 0, pr
    assert pr["data"].get("dry_run") is not False
    assert not list((Path(home) / ".state" / "archive").rglob("*.md")) if (Path(home) / ".state" / "archive").exists() else True
    # непрочитанное — в иммун, не в кандидаты
    cands = json.dumps(pr["data"], ensure_ascii=False)
    ids = [i for i in (Path(home) / "mail" / other / "inbox").glob("*.md")]
    for i in ids:
        txt = i.read_text(encoding="utf-8")
        if "msg_status: unread" in txt:
            assert txt.split("id:")[1].split("\n")[0].strip() not in cands or "immune" in cands


def test_metrics_envelope(run, home, profile):
    code, out, _ = run("metrics")
    assert code == 0
    assert "mail_sent" in out["data"] or "mail" in json.dumps(out["data"])


def test_export_verify_and_tamper(run, home, profile, other):
    code, out, _ = run("mail", "send", "--to", other, "--subject", "экспорт", "--body", "тело")
    mid = out["data"]["id"]
    f = Path(home) / "exp.json"
    code, ex, _ = run("export", "--ref", mid, "--out", str(f))
    assert code == 0, ex
    assert f.exists()
    code, v, _ = run("export", "--verify", str(f))
    assert code == 0 and v["data"].get("ok") is not False, v
    raw = json.loads(f.read_text(encoding="utf-8"))
    # портим полезную нагрузку, не сам обёрточный sha файла
    body_key = "body" if "body" in raw else ("record" if "record" in raw else None)
    if body_key:
        target = raw if body_key in raw else raw["record"]
        target[body_key] = str(target.get(body_key, "")) + " TAMPERED"
        f.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        code, v2, _ = run("export", "--verify", str(f), check=False)
        assert code != 0 or v2.get("data", {}).get("ok") is False or v2.get("status") != "ok", v2


def test_dnd_policy_blocks_and_allows_wake(run, home, profile, other):
    # цель в DND
    code, out, _ = run("dnd", "set", "--until", "2099-01-01T00:00:00Z",
                       profile_override=other)
    assert code == 0, out
    # политика пусто: today-письмо не будит
    code, m1, _ = run("mail", "send", "--to", other, "--subject", "s1", "--body", "b1",
                      "--priority", "today")
    w1 = m1["data"].get("wake", {})
    assert w1.get("triggered") is False and w1.get("suppressed") == "target_dnd", m1
    # critical в политике — будит
    pol = Path(home) / ".state" / "dnd_policy.json"
    pol.parent.mkdir(parents=True, exist_ok=True)
    pol.write_text(json.dumps([{"kind": "mail", "priority": "critical"}]), encoding="utf-8")
    code, m2, _ = run("mail", "send", "--to", other, "--subject", "s2", "--body", "b2",
                      "--priority", "critical", "--wake")
    assert m2["data"].get("wake", {}).get("triggered") is True, m2


def test_owner_watch_cursor(run, home, profile):
    code, o1, _ = run("owner", "watch")
    assert code == 0
    cur = Path(home) / ".state" / "owner_cursor.json"
    assert cur.exists(), "first run must create cursor"
    code, out, _ = run("log", "новая запись для вотча", "--numbers-ok", "--status", "ok")
    code, o2, _ = run("owner", "watch")
    assert code == 0
    assert o2["data"].get("journal") or o2["data"].get("new") or o2["data"].get("entries"), o2


def test_doctor_artifacts_reconcile(run, home, profile, tmp_path):
    art = tmp_path / "real.txt"
    art.write_text("x", encoding="utf-8")
    code, out, _ = run("log", "запись с артефактом", "--numbers-ok", "--status", "done",
                       "--artifacts", str(art))
    assert code == 0
    code, d, _ = run("doctor", "--artifacts")
    assert code in (0, 1), d
    blob = json.dumps(d["data"], ensure_ascii=False)
    assert "artifact" in blob.lower() or "reconcil" in blob.lower()
