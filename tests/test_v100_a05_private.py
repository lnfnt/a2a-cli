"""v1.0.0 §1 A-05: private (L1) journal entries."""
from __future__ import annotations

import json
from pathlib import Path


def test_private_journal_not_in_search_without_include(run, home, profile, other):
    code, out, _ = run("log", "private search note", "--private")
    assert code == 0, out
    jid = out["data"]["id"]
    run("index", "--rebuild")
    code, res, _ = run("search", "private", profile_override=other)
    assert code == 0, res
    assert all(r.get("id") != jid for r in res["data"].get("results", [])), res
    # owner still sees own private entry
    code, res2, _ = run("search", "private")
    assert code == 0, res2
    assert any(r.get("id") == jid for r in res2["data"].get("results", [])), res2


def test_private_journal_not_in_user_feed(run, home, profile, other):
    code, out, _ = run("log", "private user note", "--private")
    assert code == 0, out
    run("index", "--rebuild")
    code, feed, _ = run("feed", profile_override=other)
    assert code == 0, feed
    assert "private user note" not in json.dumps(feed, ensure_ascii=False)
    code, rd, _ = run("read", profile_override=other)
    assert code == 0, rd
    assert "private user note" not in json.dumps(rd, ensure_ascii=False)


def test_private_flag_roundtrip(run, home, profile):
    code, out, _ = run("log", "roundtrip private", "--private")
    assert code == 0, out
    assert out["data"].get("private") is True
    code, j, _ = run("journal", "--mine", "--private")
    assert code == 0, j
    assert j["data"]["count"] >= 1
    assert any(e.get("id") == out["data"]["id"] for e in j["data"].get("entries", []))
    assert all(e.get("private") for e in j["data"].get("entries", []))
