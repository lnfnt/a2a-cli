"""A2A_HOME contract regressions: shared default and explicit override."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHARED_HOME = Path.home() / ".a2a"


def _run(home: str | None, text: str, default_home: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["A2A_PROFILE"] = "a2a-home-test"
    if home is None:
        env.pop("A2A_HOME", None)
    else:
        env["A2A_HOME"] = home
    return subprocess.run(
        [sys.executable, "-m", "a2a.cli", "think", text],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _payload(proc: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(proc.stdout)


def test_home_resolver_uses_shared_default_for_unset_empty_and_whitespace() -> None:
    from a2a.util import resolve_home

    assert resolve_home({}) == SHARED_HOME
    assert resolve_home({"A2A_HOME": ""}) == SHARED_HOME
    assert resolve_home({"A2A_HOME": "   "}) == SHARED_HOME


def test_home_resolver_keeps_explicit_override_and_expands_home() -> None:
    from a2a.util import resolve_home

    assert resolve_home({"A2A_HOME": "/tmp/a2a-override"}) == Path("/tmp/a2a-override")
    assert resolve_home({"A2A_HOME": "~/a2a-override"}) == Path.home() / "a2a-override"


def test_cli_runs_without_a2a_home_using_resolved_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from a2a import cli, util

    isolated_default = tmp_path / "isolated-default"
    monkeypatch.setattr(util, "HOME", isolated_default)
    monkeypatch.setattr(util, "HOME_CONFIGURATION_ERROR", None)
    monkeypatch.delenv("A2A_HOME", raising=False)
    monkeypatch.setenv("A2A_PROFILE", "a2a-home-test")

    result = cli.main(["think", "default store works"])
    assert result == 0
    assert list((isolated_default / "thoughts" / "a2a-home-test" / "l1").glob("*.md"))


def test_explicit_a2a_home_is_an_override(tmp_path: Path) -> None:
    override = tmp_path / "alternate-store"
    proc = _run(str(override), "explicit home wins", tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _payload(proc)["ok"] is True
    assert list((override / "thoughts" / "a2a-home-test" / "l1").glob("*.md"))


def test_explicit_non_directory_a2a_home_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "not-a-directory"
    bad.write_text("occupied", encoding="utf-8")
    proc = _run(str(bad), "invalid home rejected", tmp_path)
    assert proc.returncode != 0
    assert proc.stdout
    assert _payload(proc)["ok"] is False
    assert _payload(proc)["reason"] == "a2a_home_not_directory"
