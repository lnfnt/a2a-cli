"""v0.9.0 §7 (G-09): schema migrations."""
import json
from pathlib import Path

import a2a.util as u


def _old_record(home, rid="oldrec"):
    # hand-crafted pre-v0.9 record without record_version
    d = home / "journal" / "2025" / "01"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"2025-01-01_tester_{rid}.md"
    meta = {
        "type": "journal",
        "id": rid,
        "author": "tester",
        "access": "L2",
        "created_at": "2025-01-01T00:00:00Z",
        "what_done": "old entry",
        "status": "ok",
        "artifacts": [],
    }
    import yaml
    text = "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\nbody\n"
    path.write_text(text, encoding="utf-8")
    return path, rid


def test_old_file_detected(run, home, profile):
    path, rid = _old_record(home)
    code, out, err = run("migrate", "--dry-run")
    assert code == 0, (out, err)
    assert out["data"]["count"] >= 1
    assert str(path) in out["data"]["files"] or rid in (out["data"].get("ids") or [])


def test_migrate_yes_adds_record_version(run, home, profile):
    path, rid = _old_record(home)
    code, out, err = run("migrate", "--yes")
    assert code == 0, (out, err)
    rec = u.read_record(path)
    assert rec is not None
    assert rec.get("record_version") == "0.9"


def test_migrate_repeat_noop(run, home, profile):
    path, rid = _old_record(home)
    run("migrate", "--yes")
    code, out, err = run("migrate", "--yes")
    assert code == 0, (out, err)
    assert out["data"]["count"] == 0
    assert out["data"]["no_op"] is True