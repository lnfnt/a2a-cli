"""v0.7.0 §4 (I-01): helping errors carry hint + example."""
from __future__ import annotations


def _has_help(out):
    data = out.get("data") or {}
    return "hint" in data and "example" in data


def test_invalid_choice_hint_example(run, home, profile):
    code, out, _ = run("lock", "acquire", "--scope", "nope", "--ttl", "60", check=False)
    assert code != 0
    assert out["reason"] == "invalid_choice"
    assert _has_help(out)


def test_invalid_actor_hint_example(run, home, profile, other):
    code, out, _ = run("task", "new", "job")
    tid = out["data"]["id"]
    code, out, _ = run("task", "claim", tid, profile_override="randprof-z", check=False)
    assert code != 0
    assert "invalid_actor" in (out.get("reason") or "")
    assert _has_help(out)


def test_stale_version_hint_example(run, home, profile, other):
    code, out, _ = run("task", "new", "job")
    tid = out["data"]["id"]
    run("task", "claim", tid)
    code, out, _ = run("task", "submit", tid, "--expect-version", "999", check=False)
    assert code != 0
    assert out["reason"] == "stale_version"
    assert _has_help(out)


def test_numbers_without_verify_hint_example(run, home, profile):
    code, out, _ = run("log", "всего 42 багов", check=False)
    assert code != 0
    assert out["reason"] == "numbers_without_verify"
    assert _has_help(out)


def test_locked_by_other_hint_example(run, home, profile, other):
    run("lock", "acquire", "--scope", "mail", "--ttl", "60")
    code, out, _ = run("mail", "send", "--to", profile, "--subject", "x", "--body", "y",
                       profile_override=other, check=False)
    assert code != 0
    assert out["reason"] == "locked_by_other"
    assert _has_help(out)


def test_secret_detected_hint_example(run, home, profile):
    code, out, _ = run("think", "AKIA" + "A" * 16, check=False)
    assert code != 0
    assert out["reason"] == "secret_detected"
    assert _has_help(out)
