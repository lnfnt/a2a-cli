"""v0.7.0 §2 (G-03): scope locks apply on writes."""
from __future__ import annotations


def test_acquire_release_cycle(run, home, profile, other):
    code, out, _ = run("lock", "acquire", "--scope", "mail", "--ttl", "60")
    assert code == 0, out
    assert out["data"]["scope"] == "mail"
    code, out, _ = run("lock", "release", "--scope", "mail")
    assert code == 0, out
    assert out["data"]["released"] == 1


def test_foreign_write_rejected_while_locked(run, home, profile, other):
    run("lock", "acquire", "--scope", "mail", "--ttl", "60")
    code, out, _ = run("mail", "send", "--to", profile, "--subject", "x", "--body", "y",
                        profile_override=other, check=False)
    assert code != 0
    assert out["reason"] == "locked_by_other"
    assert "ttl_left" in out["data"]


def test_write_ok_after_release(run, home, profile, other):
    run("lock", "acquire", "--scope", "mail", "--ttl", "60")
    run("lock", "release", "--scope", "mail")
    code, out, _ = run("mail", "send", "--to", profile, "--subject", "x", "--body", "y",
                        profile_override=other)
    assert code == 0, out


def test_owner_write_allowed_while_locked(run, home, profile, other):
    run("lock", "acquire", "--scope", "journal", "--ttl", "60")
    code, out, _ = run("log", "owner entry", check=False)
    assert code == 0, out


def test_stale_lock_in_doctor(run, home, profile):
    run("lock", "acquire", "--scope", "mail", "--ttl", "-1")
    code, out, _ = run("doctor")
    assert any(i["type"] == "stale_locks" for i in out["data"]["issues"])
    assert out["data"]["stale_locks"]


def test_invalid_scope_rejected(run, home, profile):
    code, out, _ = run("lock", "acquire", "--scope", "nope", "--ttl", "60", check=False)
    assert code != 0
    assert out["reason"] == "invalid_choice"


def test_lock_outside_scope_unaffected(run, home, profile, other):
    run("lock", "acquire", "--scope", "mail", "--ttl", "60")
    # journal write by other still ok
    code, out, _ = run("log", "journal entry", profile_override=other)
    assert code == 0, out
