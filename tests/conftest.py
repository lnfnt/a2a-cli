"""Shared test fixtures for a2a."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """Create an isolated a2a home directory."""
    return tmp_path / "shared_workspace"


@pytest.fixture
def profile() -> str:
    return "tester"


@pytest.fixture
def other() -> str:
    return "other"


@pytest.fixture
def run(home: Path, profile: str):
    """Run the a2a CLI in an isolated home and profile."""
    env = os.environ.copy()
    env["A2A_HOME"] = str(home)
    env["A2A_PROFILE"] = profile
    # Note: keep tests away from the real NotesApp vault mirror
    env["A2A_USER_FEED"] = str(home.parent / "notesapp-mirror")

    def _run(*args, profile_override: str | None = None, home_override: Path | str | None = None, check: bool = True, stdin: str | None = None, env_extra: dict | None = None):
        env_copy = env.copy()
        if env_extra:
            env_copy.update(env_extra)
        if profile_override:
            env_copy["A2A_PROFILE"] = profile_override
        if home_override is not None:
            env_copy["A2A_HOME"] = str(home_override)
        proc = subprocess.run(
            [sys.executable, "-m", "a2a.cli", *args],
            capture_output=True,
            text=True,
            env=env_copy,
            cwd=str(Path(__file__).parent.parent),
            input=stdin,
        )
        try:
            out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except Exception:
            out = {"raw": proc.stdout}
        if check and proc.returncode != 0:
            pass  # caller checks
        return proc.returncode, out, proc.stderr

    return _run


@pytest.fixture
def make_artifact(home: Path):
    def _make(name: str = "artifact.txt", content: str = "ok") -> Path:
        path = home / "artifacts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    return _make
