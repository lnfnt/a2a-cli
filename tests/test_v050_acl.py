"""v0.5.0: record ACL + profiles manifest (DESIGN-A2A-V05 §4).

Invariants:
- records WITHOUT an acl field behave exactly as v0.4.10 — deep-equal check
  on a fixed fixture bus (no narrowing of existing queries);
- foreign L1 stays hidden without the flag, visible with --include-l1 +
  audit event (contract preserved);
- explicit acl narrows; @all-lex/@owner tokens;
- profiles.json manifest: unknown author/recipient refused; doctor flags
  typos in EXISTING paths.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = str(Path(__file__).resolve().parent.parent)

MANIFEST = {
    "profiles": [
        {"name": name, "role": "peer-agent"}
        for name in ("coordinator", "colleague", "user-profile", "support",
                     "agent-profile", "infra")
    ],
    "channels": ["user-feed"],
}


def _write_manifest(home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "profiles.json").write_text(json.dumps(MANIFEST), encoding="utf-8")


def test_no_manifest_legacy_bus_unaffected(run, home, other):
    # tester/other are NOT in any default manifest — without profiles.json
    # everything works exactly like v0.4.10 (no narrowing).
    code, out, err = run("mail", "send", "--to", other, "--subject", "S", "--body", "b")
    assert code == 0, err
    code2, out2, err2 = run("log", "запись")
    assert code2 == 0, err2
    code3, out3, _ = run("journal", "--all", profile_override=other)
    assert code3 == 0
    assert out3["data"]["count"] == 1


def test_manifest_unknown_recipient_refused(run, home):
    _write_manifest(home)
    code, out, err = run("mail", "send", "--to", "typo1", "--subject", "S",
                         "--body", "b", profile_override="coordinator")
    assert code != 0
    assert "unknown_profile" in (out.get("reason") or ""), out
    assert not (home / "mail" / "typo1").exists()


def test_manifest_unknown_caller_refused(run, home):
    _write_manifest(home)
    code, out, err = run("log", "кто я", profile_override="ghost-profile")
    assert code != 0
    assert "unknown_profile" in (out.get("reason") or "")


def test_manifest_known_profiles_pass(run, home):
    _write_manifest(home)
    code, out, err = run("mail", "send", "--to", "user-profile", "--subject", "S",
                         "--body", "b", "--priority", "fyi", profile_override="coordinator")
    assert code == 0, (out, err)


def test_doctor_flags_existing_path_typos(run, home):
    _write_manifest(home)
    # stray mailbox + record with unknown author/recipient — placed as a
    # pre-existing data defect (doctor must catch typos on read, not write)
    stray = home / "mail" / "typo2" / "inbox"
    stray.mkdir(parents=True)
    rec = (
        "---\ntype: mail\nid: aabbccddeeff00112233445566778899\n"
        "author: typo2\nto: coordinator\naccess: L2\nsubject: typo\n"
        "msg_status: read\ncreated_at: 2026-10-01T00:00:00Z\n---\nbody\n"
    )
    (stray / "2026-10-01_x.md").write_text(rec, encoding="utf-8")
    code, out, err = run("doctor", profile_override="coordinator")
    assert code == 2  # EXIT_DEGRADED — honest flag, not silent
    issues = out["data"]["issues"]
    mt = [i for i in issues if i.get("type") == "manifest_unknown_profile"]
    assert mt, issues
    samples = json.dumps(mt)
    assert "typo2" in samples


def test_foreign_l1_gating_and_audit_for_tasks(run, home, other, profile):
    # task created L1 by profile -> invisible to other; flag reveals + audits
    code, out, err = run("task", "new", "секретная задача l1", "--access", "L1")
    assert code == 0, err
    tid = out["data"]["id"]
    code2, l, _ = run("task", "list", profile_override=other)
    assert code2 == 0
    assert all(e["id"] != tid for e in l["data"]["entries"])
    code3, b, _ = run("task", "board", profile_override=other)
    assert code3 == 0
    assert "секретная задача l1" not in json.dumps(b, ensure_ascii=False)
    code4, out4, err4 = run("task", "list", "--include-l1", profile_override=other)
    assert code4 == 0, err4
    assert any(e["id"] == tid for e in out4["data"]["entries"])
    audit = (home / ".state" / "audit.log").read_text(encoding="utf-8")
    assert "task_l1_read" in audit
    # author always sees own L1 without flag
    code5, own, _ = run("task", "list")
    assert any(e["id"] == tid for e in own["data"]["entries"])


def test_explicit_acl_narrows_journal(run, home):
    # journal entry with explicit acl = author-only, even at L2
    code, out, err = run("log", "acl-записи только для меня", "--access", "L2")
    assert code == 0, err
    jid = out["data"]["id"]
    from a2a import util
    jp = Path(out["data"]["path"])
    meta, body = util.split_front_matter(jp.read_text(encoding="utf-8"))
    meta["acl"] = ["tester"]
    util.write_atomically(jp, util.join_front_matter(meta, body))
    code2, lst, _ = run("journal", "--all", profile_override="other")
    assert code2 == 0
    assert all(e["id"] != jid for e in lst["data"]["entries"]), "acl must narrow L2 record"
    code3, own, _ = run("journal", "--all")
    assert any(e["id"] == jid for e in own["data"]["entries"])
    # --include-l1 does NOT bypass explicit acl
    code4, withflag, _ = run("journal", "--all", "--include-l1", profile_override="other")
    assert all(e["id"] != jid for e in withflag["data"]["entries"])


def test_acl_all_lex_token(run, home, other):
    code, out, err = run("task", "new", "общая задача", "--acl", "@all-lex")
    assert code == 0, err
    tid = out["data"]["id"]
    code2, lst, _ = run("task", "list", profile_override=other)
    assert any(e["id"] == tid for e in lst["data"]["entries"])
    # and without the grant: task acl author-only
    code3, out3, _ = run("task", "new", "личная задача", "--acl", "tester")
    tid3 = out3["data"]["id"]
    code4, lst2, _ = run("task", "list", profile_override=other)
    assert all(e["id"] != tid3 for e in lst2["data"]["entries"])


def test_may_read_unit_matrix(tmp_path, monkeypatch):
    from a2a import util, access
    monkeypatch.setattr(util, "HOME", tmp_path)
    # legacy records: exact v0.4.10 semantics
    assert access.may_read({"access": "L2", "author": "a"}, "b") is True
    assert access.may_read({"access": "L3", "author": "a"}, "b") is True
    assert access.may_read({"access": "L1", "author": "a"}, "a") is True
    assert access.may_read({"access": "L1", "author": "a"}, "b") is False
    assert access.may_read({"access": "L1", "author": "a"}, "b", include_l1=True) is True
    # acl records
    assert access.may_read({"access": "L2", "author": "a", "acl": ["b"]}, "b") is True
    assert access.may_read({"access": "L2", "author": "a", "acl": ["b"]}, "a") is True  # author implicit
    assert access.may_read({"access": "L2", "author": "a", "acl": ["b"]}, "c") is False
    assert access.may_read({"access": "L1", "author": "a", "acl": ["b"]}, "c", include_l1=True) is False
    assert access.may_read({"access": "L2", "author": "a", "acl": ["@all-lex"]}, "z") is True
    assert access.may_read({"access": "L2", "author": "a", "owner": "o", "acl": ["@owner"]}, "o") is True
    assert access.may_read({"access": "L2", "author": "a", "owner": "o", "acl": ["@owner"]}, "x") is False
    # mail-style: recipient implicit
    assert access.may_read({"access": "L2", "author": "a", "to": "b", "acl": ["b"]}, "b") is True


def test_deep_equal_fixture_queries_unchanged(run, home, profile, other):
    """Records without acl => every pre-v0.5 query result set is exactly the
    documented v0.4.10 set (no narrowing)."""
    # fixture bus: mixed access across authors
    _, a1, _ = run("log", "alpha l1", "--access", "L1")
    ids = {"own_l1": a1["data"]["id"]}
    _, b1, _ = run("log", "other l1", "--access", "L1", profile_override=other)
    _, b2, _ = run("log", "other l2", "--access", "L2", profile_override=other)
    _, b3, _ = run("log", "other l3", "--access", "L3", profile_override=other)
    _, a2, _ = run("log", "review l2", "--access", "L2")
    ids.update(other_l1=b1["data"]["id"], other_l2=b2["data"]["id"],
               other_l3=b3["data"]["id"], own_l2=a2["data"]["id"])
    run("mail", "send", "--to", other, "--subject", "мэйл fixture", "--body", "x")

    def expect_set(name):
        code, out, err = run("journal", "--all", profile_override=name)
        assert code == 0, err
        return {e["id"] for e in out["data"]["entries"]}

    as_pecu = expect_set(profile)
    # v0.4.10 contract: L1 only for author, L2/L3 for everyone
    assert as_pecu == {ids["own_l1"], ids["own_l2"], ids["other_l2"], ids["other_l3"]}, \
        "foreign L1 visibility changed!"
    as_other = expect_set(other)
    assert as_other == {ids["other_l1"], ids["other_l2"], ids["other_l3"], ids["own_l2"]}
    # inbox/outbox listings unchanged too
    code, mb, _ = run("mail", "list", "--inbox", profile_override=other)
    assert code == 0 and mb["data"]["count"] == 1
    code, ma, _ = run("mail", "list", "--outbox")
    assert code == 0 and ma["data"]["count"] == 1
    # a2a read feed: legacy L1 rules (excludes foreign L1). read is index-based
    # (v0.4.10 contract) — rebuild first, exactly as production hooks do.
    run("index", "--rebuild")
    code, rd, err = run("read", profile_override=other)
    assert code == 0, (rd, err)
    l1_in_feed = [e for e in rd["data"]["entries"] if e["access"] == "L1" and e["author"] != other]
    assert l1_in_feed == []
