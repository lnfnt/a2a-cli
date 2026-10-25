"""v0.8.0 §1: mail threads."""
from __future__ import annotations

import json
from pathlib import Path

from a2a import util


def _send(run, home, profile, other, subject="root", body="b"):
    code, out, err = run("mail", "send", "--to", other, "--subject", subject, "--body", body,
                         profile_override=profile)
    assert code == 0, (out, err)
    return out["data"]["id"]


def test_root_mail_thread_is_self(run, home, profile, other):
    mid = _send(run, home, profile, other)
    # thread field on the delivered record == root id
    recs = []
    for p in (home / "mail").rglob("*.md"):
        r = util.read_record(p)
        if r and r.get("id") == mid:
            recs.append(r)
    assert recs, "mail record not found"
    assert recs[0].get("thread") == mid


def test_reply_inherits_thread(run, home, profile, other):
    mid = _send(run, home, profile, other)
    code, out, err = run("mail", "answer", mid, "reply text", profile_override=other)
    assert code == 0, (out, err)
    # find the reply record (authored by other)
    replies = []
    for p in (home / "mail").rglob("*.md"):
        r = util.read_record(p)
        if r and r.get("author") == other and r.get("id") != mid:
            replies.append(r)
    assert replies, "reply not delivered"
    assert replies[0].get("thread") == mid


def test_thread_command_returns_chain_of_three(run, home, profile, other):
    mid = _send(run, home, profile, other, subject="chain")
    code, out, err = run("mail", "answer", mid, "r1", profile_override=other)
    assert code == 0, (out, err)
    # reply id is the newly delivered mail authored by other -> find it
    reply_ids = [r["id"] for p in (home / "mail").rglob("*.md")
                 for r in [util.read_record(p)] if r and r.get("author") == other and r.get("id") != mid]
    assert reply_ids
    rid = reply_ids[0]
    code, out2, err2 = run("mail", "answer", rid, "r2", profile_override=profile)
    assert code == 0, (out2, err2)

    code, out3, err3 = run("mail", "thread", mid, profile_override=profile)
    assert code == 0, (out3, err3)
    entries = out3["data"]["entries"]
    assert len(entries) == 3
    subjects = [e["subject"] for e in entries]
    assert subjects[0] == "chain"
    # chronological order
    created = [e["created_at"] for e in entries]
    assert created == sorted(created)


def test_thread_command_non_participant_denied(run, home, profile, other):
    mid = _send(run, home, profile, other)
    code, out, err = run("mail", "thread", mid, profile_override="third-party-xyz")
    assert code != 0


def test_thread_command_unknown_id(run, home, profile):
    code, out, err = run("mail", "thread", "0" * 32, profile_override=profile)
    assert code != 0
    assert "not_found" in (out.get("reason") or "")
