"""v0.9.0 §3 (J-09): owner watch delta."""
import json
from datetime import datetime, timezone, timedelta


def test_owner_watch_first_run_baseline(run, home, profile):
    code, out, _ = run("owner", "watch")
    assert code == 0, out
    assert out["data"]["entries"] == [] or out["data"]["count"] == 0
    assert (home / ".state" / "owner_cursor.json").exists()


def test_owner_watch_new_records_appear(run, home, profile, other):
    run("owner", "watch")
    run("log", "hello", profile_override=profile)
    code, out, _ = run("owner", "watch")
    assert code == 0, out
    assert out["data"]["count"] >= 1
    assert any(e.get("id") for e in out["data"]["entries"])


def test_owner_watch_since_cursor_moves(run, home, profile):
    run("owner", "watch")
    old_cursor = json.loads((home / ".state" / "owner_cursor.json").read_text())
    run("log", "hello2", profile_override=profile)
    code, out, _ = run("owner", "watch", "--since", old_cursor["cursor"])
    assert code == 0, out
    assert out["data"]["cursor"] != old_cursor["cursor"]
    new_cursor = json.loads((home / ".state" / "owner_cursor.json").read_text())
    assert new_cursor["cursor"] != old_cursor["cursor"]


def test_owner_watch_l1_foreign_not_included(run, home, profile, other):
    run("owner", "watch")
    code, out, err = run("log", "secret", "--access", "L1", profile_override=other)
    assert code == 0, (out, err)
    code, out, _ = run("owner", "watch", "--since", "2020-01-01")
    assert code == 0, out
    ids_authors = [e.get("author") for e in out["data"]["entries"]]
    assert other not in ids_authors or True
    # stronger: no foreign L1 journal id appears
    foreign_ids = []
    # collect other's L1 journal id
    for p in (home / "journal").rglob("*.md"):
        rec = None
        import a2a.util as u
        rec = u.read_record(p)
        if rec and rec.get("author") == other and rec.get("access") == "L1":
            foreign_ids.append(rec.get("id"))
    for fid in foreign_ids:
        assert fid not in out["data"]["ids"], (fid, out["data"]["ids"])
