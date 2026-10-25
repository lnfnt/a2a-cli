"""v0.6.0 §6 (J-06): CAS для task-переходов — --expect-version сверяется с фактическим
version записи; mismatch = stale_version. Без флага поведение точно как раньше."""
from __future__ import annotations


def _tid(run, title="cas job", owner=None):
    args = ["task", "new", title]
    if owner:
        args += ["--owner", owner]
    code, out, _ = run(*args)
    assert code == 0, out
    return out["data"]["id"]


def test_cas_match_allows_transition(run, home, other):
    tid = _tid(run, "cas ок", owner=other)
    code, out, _ = run("task", "claim", tid, "--expect-version", "1", profile_override=other)
    assert code == 0, out
    assert out["data"]["version"] == 2


def test_cas_mismatch_rejected_stale_version(run, home, other):
    tid = _tid(run, "cas stale", owner=other)
    code, out, _ = run("task", "claim", tid, "--expect-version", "5",
                       profile_override=other, check=False)
    assert code == 1
    assert out["reason"] == "stale_version"
    assert out["data"]["expected"] == 5 and out["data"]["actual"] == 1
    # запись не тронута
    _, show, _ = run("task", "show", tid, profile_override=other)
    assert show["data"]["version"] == 1
    assert show["data"]["status"] == "assigned"


def test_cas_absent_keeps_v05_behavior(run, home, other):
    tid = _tid(run, "cas compat", owner=other)
    code, out, _ = run("task", "claim", tid, profile_override=other)
    assert code == 0, out


def test_cas_stale_after_race_on_submit(run, home, other):
    tid = _tid(run, "cas race", owner=other)
    run("task", "claim", tid, profile_override=other)  # v2
    code, out, _ = run("task", "submit", tid, "--expect-version", "1",
                       profile_override=other, check=False)
    assert code == 1
    assert out["reason"] == "stale_version"
    assert out["data"]["actual"] == 2
    code2, out2, _ = run("task", "submit", tid, "--expect-version", "2", profile_override=other)
    assert code2 == 0, out2
    assert out2["data"]["status"] == "review"


def test_cas_invalid_value_usage_error(run, home, other):
    tid = _tid(run, "cas bad arg", owner=other)
    code, out, _ = run("task", "claim", tid, "--expect-version", "not-a-number",
                       profile_override=other, check=False)
    assert code == 4
    assert "expect_version" in (out.get("reason") or "")
