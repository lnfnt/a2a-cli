"""v0.2.5: supersede updates proof expectation without rewriting history."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path


def _cli(home: Path, *args: str):
    env = os.environ.copy()
    env["A2A_HOME"] = str(home)
    env["A2A_PROFILE"] = "tester"
    env["A2A_USER_FEED"] = str(home.parent / "notesapp-mirror")
    src = str(Path(__file__).resolve().parent.parent)
    return subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, %r); from a2a.cli import main; sys.exit(main())" % src, *args],
        capture_output=True, text=True, env=env,
    )


def test_verify_supersede_cycle(tmp_path):
    home = tmp_path / "store"
    (home / "journal" / "2026" / "09").mkdir(parents=True)
    art = tmp_path / "artifact.txt"
    art.write_text("v1")
    sha1 = hashlib.sha256(b"v1").hexdigest()
    jid = "testsupersede0001"
    rec = (
        "---\n"
        "type: journal\n"
        "author: tester\n"
        "access: L1\n"
        "what_done: test\n"
        "artifacts: []\n"
        "artifact_proofs:\n"
        f"- path: {art}\n"
        "  exists: true\n"
        "  mtime: '2026-09-30T00:00:00+00:00'\n"
        f"  sha256: {sha1}\n"
        "verify: manual\n"
        "status: ok\n"
        f"id: {jid}\n"
        "created_at: '2026-09-30T00:00:00Z'\n"
        "---\n"
        "body\n"
    )
    recp = home / "journal" / "2026" / "09" / "2026-09-30_tester_x.md"
    recp.write_text(rec, encoding="utf-8")

    # 1) artifact legitimately changes, verify flags it
    art.write_text("v2")
    r = _cli(home, "verify")
    assert "artifact_changed" in r.stdout, r.stdout

    # 3) supersede
    r = _cli(home, "verify", "--supersede", jid, "--artifact", str(art), "--reason", "legit edit")
    import json
    d = json.loads(r.stdout)
    assert d["ok"] and d["data"]["superseded"] is True, r.stdout

    # 4) verify: clean
    r = _cli(home, "verify")
    d = json.loads(r.stdout)
    assert d["ok"], r.stdout
    assert d["data"]["superseded_ok"] == 1

    # 5) history preserved: old sha still present, superseded block added
    txt = recp.read_text(encoding="utf-8")
    assert sha1 in txt
    assert "artifact_proofs_superseded" in txt

    # 6) idempotent supersede to same sha is a no-op
    r = _cli(home, "verify", "--supersede", jid, "--artifact", str(art), "--reason", "again")
    d = json.loads(r.stdout)
    assert d["ok"] and d["data"]["superseded"] is False
