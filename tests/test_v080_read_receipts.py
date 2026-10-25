"""v0.8.0 §5: mail read receipts (lite)."""
from __future__ import annotations

import json
from pathlib import Path

from a2a import util


def _send(run, home, profile, other, subject="root", body="b"):
    code, out, err = run("mail", "send", "--to", other, "--subject", subject, "--body", body,
                          profile_override=profile)
    assert code == 0, (out, err)
    return out["data"]["id"]


def test_mail_read_writes_receipt_event(run, home, profile, other):
    mid = _send(run, home, profile, other)
    code, out, err = run("mail", "read", mid, profile_override=other)
    assert code == 0, (out, err)
    log = home / ".state" / "read_receipts.log"
    assert log.exists()
    events = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert any(e.get("mail_id") == mid and e.get("reader") == other for e in events)
    ev = [e for e in events if e.get("mail_id") == mid][0]
    assert "ts" in ev


def test_mail_receipt_returns_readers(run, home, profile, other):
    mid = _send(run, home, profile, other)
    run("mail", "read", mid, profile_override=other)
    code, out, err = run("mail", "receipt", mid, profile_override=profile)
    assert code == 0, (out, err)
    data = out["data"]
    assert other in data["readers"]
    assert data["count"] >= 1
    assert any(e.get("reader") == other for e in data["entries"])


def test_mail_receipt_non_author_denied(run, home, profile, other):
    mid = _send(run, home, profile, other)
    run("mail", "read", mid, profile_override=other)
    code, out, err = run("mail", "receipt", mid, profile_override="third-party-xyz")
    assert code != 0


def test_mail_receipt_unknown_id(run, home, profile):
    code, out, err = run("mail", "receipt", "0" * 32, profile_override=profile)
    assert code != 0
    assert "not_found" in (out.get("reason") or "")
