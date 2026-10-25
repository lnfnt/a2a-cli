"""v1.0.0 §3 I-02: onboard master for a profile."""
from __future__ import annotations


def test_onboard_new_profile(run, home):
    code, out, err = run("onboard", "--profile", "newbie")
    assert code == 0, (out, err)
    assert (home / "mail" / "newbie").is_dir()
    assert (home / "thoughts" / "newbie").is_dir()
    assert out["data"].get("doctor_status") == "ok" or out["data"].get("status") == "ok"
    cmds = (out["data"].get("next_commands") or out["data"].get("next")
            or out["data"].get("commands"))
    assert isinstance(cmds, list) and len(cmds) == 3


def test_onboard_idempotent(run, home):
    run("onboard", "--profile", "newbie")
    code, out, err = run("onboard", "--profile", "newbie")
    assert code == 0, (out, err)
    assert out["data"].get("no_change") is True or out["data"].get("changed") is False


def test_onboard_doctor_green(run, home):
    code, out, _ = run("onboard", "--profile", "newbie")
    assert code == 0, out
    code2, d, _ = run("doctor")
    assert code2 == 0, d
    assert (d.get("status") == "ok") or (d.get("data", {}).get("status") == "ok")
