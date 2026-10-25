"""v0.7.0 §3 (G-08): жизненный цикл секретов."""
from __future__ import annotations

from pathlib import Path

KV_VAL = "Sup3rKeyValUE999"


def _trigger(run, home, profile=None, channel="mail"):
    if channel == "mail":
        return run("mail", "send", "--to", "other" if profile is None else profile,
                   "--subject", f"deploy api_key={KV_VAL}", "--body", "plain text")
    if channel == "journal":
        return run("log", f"rotated api_key={KV_VAL} in journal")
    return None


def test_secrets_report_counts_events(run, home, profile, other):
    # два события в разных каналах
    run("mail", "send", "--to", other, "--subject", f"deploy api_key={KV_VAL}", "--body", "x")
    run("log", f"rotated api_key={KV_VAL} in journal")
    code, out, _ = run("secrets", "report")
    assert code == 0, out
    data = out["data"]
    assert data.get("total", data.get("count", data.get("events"))) == 2
    channels = (data.get("by_channel") or data.get("channels") or data.get("counts_by_channel")
                or data.get("per_channel"))
    assert channels is not None
    assert channels.get("mail_send", 0) >= 1
    assert channels.get("journal", 0) >= 1


def test_secrets_report_no_content(run, home, profile, other):
    run("mail", "send", "--to", other, "--subject", f"deploy api_key={KV_VAL}", "--body", "x")
    code, out, _ = run("secrets", "report")
    assert code == 0, out
    text = str(out)
    assert KV_VAL not in text


def test_secrets_report_week_bucket(run, home, profile, other):
    run("mail", "send", "--to", other, "--subject", f"deploy api_key={KV_VAL}", "--body", "x")
    code, out, _ = run("secrets", "report")
    assert code == 0, out
    data = out["data"]
    weeks = (data.get("by_week") or data.get("weeks") or data.get("weekly_counts")
             or data.get("counts_by_week"))
    assert weeks is not None and len(weeks) >= 1


def test_notify_creates_flag(run, home, profile, other):
    run("mail", "send", "--to", other, "--subject", f"deploy api_key={KV_VAL}", "--body", "x")
    code, out, _ = run("secrets", "report", "--notify")
    assert code == 0, out
    flag = home / ".state" / "secrets_watch"
    assert flag.exists()


def test_doctor_secret_spike(run, home, profile, other):
    for _ in range(4):
        run("mail", "send", "--to", other, "--subject", f"deploy api_key={KV_VAL}", "--body", "x")
    code, out, _ = run("doctor")
    issues = out["data"]["issues"]
    assert any(i.get("type") == "secret_spike" for i in issues), out


def test_secrets_report_empty(run, home):
    code, out, _ = run("secrets", "report")
    assert code == 0, out
    data = out["data"]
    total = data.get("total", data.get("count", data.get("events")))
    assert total == 0
