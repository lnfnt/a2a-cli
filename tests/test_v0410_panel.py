"""v0.4.10: регресс панели внешнего аудита (glm) — archive-путь, L1-утечка, parse_utc."""
import json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

REPO = str(Path(__file__).resolve().parent.parent)

def run(bus, profile, *args, code=None):
    p = subprocess.run([sys.executable, "-m", "a2a.cli", *args],
                       env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
                            "A2A_HOME": str(bus), "A2A_PROFILE": profile},
                       capture_output=True, text=True, timeout=60, cwd=REPO)
    if code is not None:
        assert p.returncode == code, (args, p.returncode, p.stdout[-300:])
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"raw": p.stdout}


def test_journal_all_no_longer_leaks_foreign_l1(tmp_path):
    bus = tmp_path / "bus"
    run(bus, "coordinator", "log", "своя мысль", "--access", "L1")
    run(bus, "user-profile", "log", "чужая мысль", "--access", "L1")
    entries = run(bus, "coordinator", "journal", "--all")["data"]["entries"]
    assert all("чужая" not in e["what_done"] for e in entries)
    with_flag = run(bus, "coordinator", "journal", "--include-l1")["data"]["entries"]
    assert any("чужая" in e["what_done"] for e in with_flag)
    audit = (bus / ".state" / "audit.log").read_text()
    assert "journal_l1_read" in audit


def test_mail_archive_visible_to_index(tmp_path):
    bus = tmp_path / "bus"
    run(bus, "coordinator", "mail", "send", "--to", "user-profile", "--subject", "S10", "--body", "body")
    # FIXTURE FIX (v0.5.0 work): the original test replaced the hard-coded
    # date "2026-10-22" (the day it was written). It is a time bomb: from
    # 2026-10-23 on it never matched and the test failed before any v0.5
    # change existed. Same intent, dynamic date — all assertions unchanged.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for p in (bus / "mail" / "user-profile" / "inbox").glob("*.md"):
        p.write_text(p.read_text().replace(today, "2015-01-01"))
    r = run(bus, "user-profile", "mail", "archive")
    assert r["data"]["archived"] == 1
    assert (bus / "mail" / "user-profile" / "archive").exists()
    sr = run(bus, "user-profile", "search", "S10")
    assert sr["data"]["count"] >= 1  # архив остаётся в индексе


def test_parse_utc_returns_aware():
    from a2a import util
    from datetime import datetime
    d = util.parse_utc("2026-10-22T17:24:34Z")
    assert d.tzinfo is not None
    assert d < datetime.now(d.tzinfo)  # сравнение с aware не падает
    n = util.parse_utc("2026-10-22T17:24:34")  # naive input -> aware output
    assert n.tzinfo is not None
