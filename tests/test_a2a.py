"""Tests for a2a CLI and modules."""

from __future__ import annotations

import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

from a2a import cli, index, util


class TestAcceptance01Think:
    def test_think_default_l1(self, run, home, profile):
        code, out, _ = run("think", "гипотеза")
        assert code == 0
        assert out["ok"] is True
        files = list((home / "thoughts" / profile / "l1").glob("*.md"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "access: L1" in text

    def test_think_explicit_l2(self, run, home, profile):
        code, out, _ = run("think", "shared thought", "--access", "L2")
        assert code == 0
        files = list((home / "thoughts" / profile / "l2").glob("*.md"))
        assert len(files) == 1

    def test_think_topic(self, run, home, profile):
        run("think", "topic text", "--topic", "review")
        files = list((home / "thoughts" / profile / "l1").glob("*.md"))
        assert "topic: review" in files[0].read_text(encoding="utf-8")


class TestAcceptance02Journal:
    def test_log_unverified_missing_artifact(self, run, home, profile):
        code, out, _ = run("log", "done something", "--artifacts", "/nonexistent")
        assert code == 0
        assert out["data"]["status"] == "UNVERIFIED_CLAIM"
        files = list((home / "journal").rglob("*.md"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "UNVERIFIED_CLAIM" in text

    def test_log_verified_existing_artifact(self, run, make_artifact):
        art = make_artifact("done.txt")
        code, out, _ = run("log", "done task", "--artifacts", str(art))
        assert code == 0
        assert out["data"]["status"] == "ok"

    def test_log_left_and_deadline(self, run):
        code, out, _ = run("log", "done", "--left", "more", "--deadline", "2026-10-01")
        assert code == 0
        assert out["ok"] is True


class TestAcceptance03MailSelfSend:
    def test_mail_self_forbidden(self, run, profile):
        code, out, _ = run("mail", "send", "--to", profile, "--subject", "x")
        assert code == 4
        assert out["reason"] == "self_send_forbidden"

    def test_mail_delivers_to_inbox(self, run, home, profile, other):
        code, out, _ = run("mail", "send", "--to", other, "--subject", "hello", "body text")
        assert code == 0
        assert (home / "mail" / other / "inbox").exists()
        files = list((home / "mail" / other / "inbox").glob("*.md"))
        assert len(files) == 1

    def test_mail_priority(self, run):
        code, out, _ = run("mail", "send", "--to", "other", "--subject", "urgent", "--priority", "today")
        assert code == 0
        assert out["data"]["priority"] == "today"


class TestAcceptance04Scrubber:
    def test_bearer_detected_without_echoing_value(self, run, home, profile):
        token = "Bearer " + "eyJ" + "A" * 10 + "." + "B" * 10 + "." + "C" * 32
        code, out, err = run("think", f"token {token}")
        assert code == 1
        assert out["reason"] == "secret_detected"
        assert token not in json.dumps(out) + err
        files = list((home / "thoughts" / profile / "l1").glob("*.md"))
        assert len(files) == 0

    def test_messenger_bot_token_detected(self, run, home, profile):
        token = "1" * 10 + ":" + "T" * 32
        code, out, err = run("think", f"bot {token}")
        assert code == 1
        assert out["reason"] == "secret_detected"
        assert token not in json.dumps(out) + err
        files = list((home / "thoughts" / profile / "l1").glob("*.md"))
        assert len(files) == 0

    def test_gocspx_detected(self, run, home, profile):
        token = "GOCSPX-" + "Q" * 24
        code, out, err = run("think", token)
        assert code == 1
        assert out["reason"] == "secret_detected"
        assert token not in json.dumps(out) + err
        files = list((home / "thoughts" / profile / "l1").glob("*.md"))
        assert len(files) == 0

    def test_aws_key_detected(self, run, home, profile):
        token = "AKIA" + "A" * 16
        code, out, err = run("think", token)
        assert code == 1
        assert out["reason"] == "secret_detected"
        assert token not in json.dumps(out) + err
        files = list((home / "thoughts" / profile / "l1").glob("*.md"))
        assert len(files) == 0

    def test_hermes_path_detected(self, run, home, profile):
        code, out, _ = run("think", "path /home/alex/.hermes/auth.json")
        assert code == 1
        assert out["reason"] == "secret_detected"
        files = list((home / "thoughts" / profile / "l1").glob("*.md"))
        assert len(files) == 0


class TestAcceptance05L1Search:
    def test_l1_isolation_and_audit(self, run, home, profile, other):
        # profile creates L1 thought
        run("think", "private alpha")
        # rebuild index
        run("index", "--rebuild")
        # other searches
        code, out, _ = run("search", "alpha", profile_override=other)
        assert code == 0
        ids = [r["id"] for r in out["data"]["results"]]
        assert not ids
        # owner searches
        code2, out2, _ = run("search", "alpha")
        assert code2 == 0
        assert any("alpha" in (r.get("snippet") or "") for r in out2["data"]["results"])
        # audit log records both accesses to L1
        audit_path = home / ".state" / "audit.log"
        assert audit_path.exists()
        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line) for line in lines]
        l1_events = [e for e in events if e["event"] == "search_l1"]
        assert len(l1_events) >= 1


class TestAcceptance06JournalAggregate:
    def test_journal_today_all(self, run, profile, other):
        run("log", "entry one")
        run("log", "entry two", profile_override=other)
        run("index", "--rebuild")
        code, out, _ = run("journal", "--today", "--all")
        assert code == 0
        authors = {e["author"] for e in out["data"]["entries"]}
        assert profile in authors
        assert other in authors


class TestAcceptance07LockConflict:
    def test_lock_conflict_active_other(self, run, profile, other):
        run("lock", "set", "--target", "db", "--reason", "migration", "--expire", "10m", profile_override=other)
        code, out, _ = run("lock", "set", "--target", "db", "--reason", "backup", "--expire", "10m")
        assert code == 1
        assert out["reason"] == "lock_conflict"

    def test_lock_no_conflict_expired_other(self, run, profile, other):
        run("lock", "set", "--target", "db", "--reason", "old", "--expire=-1m", profile_override=other)
        code, out, _ = run("lock", "set", "--target", "db", "--reason", "new", "--expire", "10m")
        assert code == 0

    def test_lock_same_profile_allowed(self, run):
        run("lock", "set", "--target", "svc", "--reason", "first", "--expire", "10m")
        code, out, _ = run("lock", "set", "--target", "svc", "--reason", "second", "--expire", "10m")
        assert code == 0


class TestAcceptance08DND:
    def test_dnd_check_active(self, run):
        run("dnd", "set", "--until", "2099-12-31T23:59:59Z")
        code, out, _ = run("dnd", "check")
        assert code == 0
        assert out["data"]["active"] is True
        assert out["data"]["until"]

    def test_dnd_clear(self, run):
        run("dnd", "set", "--until", "2099-12-31T23:59:59Z")
        run("dnd", "clear")
        code, out, _ = run("dnd", "check")
        assert code == 0
        assert out["data"]["active"] is False


class TestAcceptance09InvalidRecord:
    def test_manual_missing_id_rejected(self, run, home, profile):
        path = home / "thoughts" / profile / "l2"
        path.mkdir(parents=True)
        bad_file = path / "bad.md"
        bad_file.write_text("---\ntype: thought\nauthor: tester\naccess: L2\ncreated_at: 2026-09-25T00:00:00Z\n---\nbody\n")
        run("index", "--rebuild")
        code, out, _ = run("verify")
        assert code == 3

    def test_invalid_type_rejected(self, run):
        # CLI filters types, so test module directly
        from a2a import schema
        ok, reason = schema.validate({"id": "x", "type": "bogus", "author": "a", "access": "L2", "created_at": "2026-09-25T00:00:00Z"}, "")
        assert not ok


class TestAcceptance10IndexRebuild:
    def test_rebuild_counts_match(self, run, home, profile, other):
        run("think", "a")
        run("log", "b")
        run("mail", "send", "--to", other, "--subject", "c")
        run("user", "post", "d")
        code, out, _ = run("index", "--rebuild")
        assert code == 0
        counts = out["data"]["counts"]
        assert counts.get("thought") == 1
        assert counts.get("journal") == 1
        assert counts.get("mail") == 1
        assert counts.get("user-note") == 1


class TestAcceptance11Promote:
    def test_promote_l1_to_l2_new_record(self, run, home, profile):
        run("think", "raw idea")
        files = list((home / "thoughts" / profile / "l1").glob("*.md"))
        original = files[0]
        original_text = original.read_text(encoding="utf-8")
        original_id = None
        for line in original_text.splitlines():
            if line.startswith("id:"):
                original_id = line.split(":", 1)[1].strip()
                break
        # publish to L2 (new thought or user post). Use think --access L2 with ref.
        run("think", "polished idea", "--access", "L2", "--topic", f"ref:{original_id}")
        l2_files = list((home / "thoughts" / profile / "l2").glob("*.md"))
        assert len(l2_files) == 1
        # original unchanged
        assert original.read_text(encoding="utf-8") == original_text




class TestFailClosed:
    def test_no_access_defaults_l1(self, run, home, profile):
        # manually create record without access -> schema defaults to L1 on CLI
        run("think", "no access flag")
        files = list((home / "thoughts" / profile / "l1").glob("*.md"))
        assert len(files) == 1
        assert "access: L1" in files[0].read_text(encoding="utf-8")

    def test_invalid_access_read_rejected(self, run):
        from a2a import schema
        ok, reason = schema.validate({"id": "x", "type": "thought", "author": "a", "access": "L0", "created_at": "2026-09-25T00:00:00Z"}, "b")
        assert not ok
        assert "access" in reason

    def test_invalid_created_at_rejected(self, run):
        from a2a import schema
        ok, reason = schema.validate({"id": "x", "type": "thought", "author": "a", "access": "L2", "created_at": "not-a-date"}, "b")
        assert not ok
        assert "created_at" in reason

    def test_secret_detected_file_not_created(self, run, home, profile):
        token = "github_pat_" + "M" * 12 + "_" + "N" * 12
        code, out, err = run("think", token)
        assert code == 1
        assert out["reason"] == "secret_detected"
        assert token not in json.dumps(out) + err
        files = list((home / "thoughts" / profile / "l1").glob("*.md"))
        assert len(files) == 0

    def test_self_send_exit_4(self, run, profile):
        code, _, _ = run("mail", "send", "--to", profile, "--subject", "x")
        assert code == 4

    def test_doctor_finds_orphan(self, run, home):
        orphan = home / "journal" / "2026" / "09" / "orphan.md"
        orphan.parent.mkdir(parents=True)
        # orphan = file present in storage but not parseable/indexable
        orphan.write_text("not a valid front matter record\n")
        code, out, _ = run("doctor")
        assert code == 2
        issues = out["data"]["issues"]
        assert any(i["type"] == "orphan_files" for i in issues)

    def test_no_l1_to_l2_downgrade(self, run, home, profile):
        # create L2 then try to write L1 - CLI default by type, explicit wins.
        run("log", "public", "--access", "L2")
        files = list((home / "journal").rglob("*.md"))
        text = files[0].read_text(encoding="utf-8")
        assert "access: L2" in text
        # cannot mutate existing; only new records


class TestMailLifecycle:
    def test_mail_read_marks_read(self, run):
        run("mail", "send", "--to", "other", "--subject", "s", "body")
        code, out, _ = run("mail", "list", "--inbox", profile_override="other")
        mid = out["data"]["entries"][0]["id"]
        code2, out2, _ = run("mail", "read", mid, profile_override="other")
        assert code2 == 0
        assert out2["data"]["msg_status"] == "read"

    def test_mail_answer(self, run, profile):
        run("mail", "send", "--to", "other", "--subject", "q", "body")
        code, out, _ = run("mail", "list", "--inbox", profile_override="other")
        mid = out["data"]["entries"][0]["id"]
        run("mail", "answer", mid, "reply", profile_override="other")
        code2, out2, _ = run("mail", "list", "--inbox")
        assert any("Re:" in e["subject"] for e in out2["data"]["entries"])

    def test_mail_archive_old(self, run, home):
        # create old mail by backdating manually in profile's own inbox
        old = home / "mail" / "tester" / "inbox" / "old.md"
        old.parent.mkdir(parents=True)
        old.write_text(
            "---\nid: old-mail\ntype: mail\nauthor: other\nto: tester\naccess: L2\ncreated_at: 2026-01-01T00:00:00Z\nsubject: old\nmsg_status: read\npriority: fyi\n---\nbody\n"
        )
        code, out, _ = run("mail", "archive", "--older-than", "30")
        assert code == 0
        assert out["data"]["archived"] == 1


class TestStatus:
    def test_status_set_and_show(self, run):
        run("status", "set", "--state", "busy", "--note", "working")
        code, out, _ = run("status", "show")
        assert code == 0
        assert any(e["state"] == "busy" for e in out["data"]["entries"])

    def test_status_invalid_state(self, run):
        code, out, _ = run("status", "set", "--state", "flying")
        assert code == 3


class TestHandoff:
    def test_handoff_create(self, run, home, other):
        code, out, _ = run("handoff", "--to", other, "--task", "migrate db", "--acceptance", "tests green")
        assert code == 0
        assert (home / "records" / "handoffs").exists()

    def test_handoff_list_for(self, run, other):
        run("handoff", "--to", other, "--task", "t", "--acceptance", "a")
        code, out, _ = run("handoff", "--for", other)
        assert code == 0
        assert out["data"]["count"] == 1


class TestUserFeed:
    def test_user_post(self, run, home):
        code, out, _ = run("user", "post", "weekly summary", "--kind", "weekly")
        assert code == 0
        files = list((home / "user-feed").glob("*.md"))
        assert len(files) == 1

    def test_user_feed_filter(self, run):
        run("user", "post", "one", "--kind", "weekly")
        run("user", "post", "two", "--kind", "fyi")
        run("index", "--rebuild")
        code, out, _ = run("feed", "--tag", "weekly")
        assert code == 0
        assert out["data"]["count"] == 1


class TestIndexAndDoctor:
    def test_index_exists(self, run):
        code, out, _ = run("index")
        assert code == 0
        assert "index_exists" in out["data"]

    def test_doctor_writes_journal(self, run, home):
        run("doctor")
        files = list((home / "journal").rglob("*.md"))
        assert any("a2a doctor run" in f.read_text(encoding="utf-8") for f in files)


class TestVerify:
    def test_verify_pass(self, run):
        run("log", "verified", "--artifacts", "/dev/null")
        run("index", "--rebuild")
        code, out, _ = run("verify")
        assert code == 0
        assert out["data"]["verified"] is True

    def test_verify_fails_on_bad_record(self, run, home):
        bad = home / "thoughts" / "tester" / "l2" / "bad.md"
        bad.parent.mkdir(parents=True)
        bad.write_text("not front matter")
        run("index", "--rebuild")
        code, out, _ = run("verify")
        assert code == 3


class TestCLI:
    def test_no_command_returns_usage_error(self, run):
        code, _, _ = run(check=False)
        assert code == 4

    def test_profile_from_env(self, run, home):
        run("think", "env profile")
        files = list((home / "thoughts" / "tester" / "l1").glob("*.md"))
        assert len(files) == 1

    def test_profile_override_flag(self, run, home):
        code, _, _ = run("think", "flag", profile_override="flagger")
        files = list((home / "thoughts" / "flagger" / "l1").glob("*.md"))
        assert len(files) == 1


class TestScrubber:
    def test_scan_empty(self):
        from a2a import scrubber
        dirty, hits = scrubber.check("hello world")
        assert dirty is False
        assert hits == []

    def test_scan_multiple(self):
        from a2a import scrubber
        value = "Bearer " + "eyJ" + "A" * 10 + "." + "B" * 10 + "." + "C" * 32 + " and AKIA" + "Z" * 16
        dirty, hits = scrubber.check(value)
        assert dirty is True
        assert set(hits) == {"bearer_token", "jwt", "aws_key"}
        assert value not in str(hits)


class TestUtil:
    def test_utc_iso(self):
        from a2a.util import utc_iso, parse_utc
        s = utc_iso()
        dt = parse_utc(s)
        assert dt.tzinfo is not None

    def test_split_front_matter(self):
        from a2a.util import split_front_matter, join_front_matter
        text = "---\nid: 1\ntype: thought\n---\nbody\n"
        meta, body = split_front_matter(text)
        assert meta["id"] == 1
        assert body == "body\n"
        restored = join_front_matter(meta, body)
        assert "---" in restored

    def test_resolve_profile_order_env_over_as(self, monkeypatch):
        from argparse import Namespace
        from a2a.util import resolve_profile
        monkeypatch.setenv("A2A_PROFILE", "envprof")
        args = Namespace(profile=None, as_="asprof")
        assert resolve_profile(args) == "envprof"

    def test_resolve_profile_arg_over_as(self, monkeypatch):
        from argparse import Namespace
        from a2a.util import resolve_profile
        monkeypatch.delenv("A2A_PROFILE", raising=False)
        args = Namespace(profile="argprof", as_="asprof")
        assert resolve_profile(args) == "argprof"

    def test_resolve_profile_from_hermes_home(self, monkeypatch):
        # Режим профилей Hermes: HERMES_HOME указывает на каталог профиля,
        # а HERMES_PROFILE не выставляется вовсе.
        from argparse import Namespace
        from a2a.util import resolve_profile
        monkeypatch.delenv("A2A_PROFILE", raising=False)
        monkeypatch.delenv("HERMES_PROFILE", raising=False)
        monkeypatch.setenv("HERMES_HOME", "/home/u/.hermes/profiles/coordinator")
        args = Namespace(profile=None, as_=None)
        assert resolve_profile(args) == "coordinator"

    def test_hermes_home_wins_over_stale_active_profile_marker(self, monkeypatch, tmp_path):
        # Регрессия: ~/.hermes/active_profile может месяцами указывать на другой
        # профиль. Именно на этом a2a молча адресовал почту чужому профилю и
        # письмо не находилось (mail_not_found).
        from argparse import Namespace
        from a2a.util import resolve_profile
        monkeypatch.delenv("A2A_PROFILE", raising=False)
        monkeypatch.delenv("HERMES_PROFILE", raising=False)
        marker = tmp_path / ".hermes"
        marker.mkdir(parents=True)
        (marker / "active_profile").write_text("support", encoding="utf-8")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(marker / "profiles" / "coordinator"))
        args = Namespace(profile=None, as_=None)
        assert resolve_profile(args) == "coordinator"

    def test_a2a_profile_env_wins_over_hermes_home(self, monkeypatch):
        # Явное перекрытие должно побеждать автодетект.
        from argparse import Namespace
        from a2a.util import resolve_profile
        monkeypatch.setenv("A2A_PROFILE", "explicit")
        monkeypatch.setenv("HERMES_HOME", "/home/u/.hermes/profiles/coordinator")
        args = Namespace(profile=None, as_=None)
        assert resolve_profile(args) == "explicit"

    def test_marker_used_when_hermes_home_absent(self, monkeypatch, tmp_path):
        # Классический режим (без профилей) должен работать как раньше.
        from argparse import Namespace
        from a2a.util import resolve_profile
        monkeypatch.delenv("A2A_PROFILE", raising=False)
        monkeypatch.delenv("HERMES_PROFILE", raising=False)
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkey = tmp_path / ".hermes" / "active_profile"
        monkey.parent.mkdir(parents=True)
        monkey.write_text("legacyprof", encoding="utf-8")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        args = Namespace(profile=None, as_=None)
        assert resolve_profile(args) == "legacyprof"

    def test_hermes_home_profiles_dir_is_not_taken_as_a_profile_name(self, monkeypatch, tmp_path):
        # ~/.hermes/profiles без имени — это не профиль; иначе почта уйдёт
        # в каталог «profiles».
        from argparse import Namespace
        from a2a.util import resolve_profile
        monkeypatch.delenv("A2A_PROFILE", raising=False)
        monkeypatch.delenv("HERMES_PROFILE", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes" / "profiles"))
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        args = Namespace(profile=None, as_=None)
        assert resolve_profile(args) != "profiles"


class TestSchema:
    def test_journal_defaults_l2(self):
        from a2a import schema
        meta = {"type": "journal", "author": "a", "what_done": "x"}
        schema.apply_defaults(meta, "journal")
        assert meta["access"] == "L2"
        assert meta["id"]
        assert meta["created_at"]

    def test_thought_defaults_l1(self):
        from a2a import schema
        meta = {"type": "thought", "author": "a"}
        schema.apply_defaults(meta, "thought")
        assert meta["access"] == "L1"

    def test_journal_status_unverified(self, tmp_path):
        from a2a import schema
        missing = tmp_path / "missing.txt"
        status = schema.journal_status({"artifacts": [str(missing)]})
        assert status == "UNVERIFIED_CLAIM"

    def test_validate_mail_missing_to(self):
        from a2a import schema
        ok, reason = schema.validate({"id": "x", "type": "mail", "author": "a", "access": "L2", "created_at": "2026-09-25T00:00:00Z", "subject": "s"}, "")
        assert not ok
        assert "to" in reason


class TestReadCommand:
    def test_read_all(self, run):
        run("log", "public")
        run("index", "--rebuild")
        code, out, _ = run("read")
        assert code == 0
        assert out["data"]["count"] >= 1

    def test_read_from_filter(self, run, other):
        run("log", "from me")
        run("log", "from other", profile_override=other)
        run("index", "--rebuild")
        code, out, _ = run("read", "--from", other)
        assert code == 0
        assert all(e["author"] == other for e in out["data"]["entries"])


class TestTarballRebuild:
    def test_source_tarball_contains_tests_and_pyproject(self, tmp_path):
        root = Path(__file__).parent.parent
        archive = tmp_path / "src.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            for p in root.rglob("*"):
                if p.is_file() and ".venv" not in str(p):
                    tf.add(p, arcname=p.relative_to(root))
        with tarfile.open(archive, "r:gz") as tf:
            names = tf.getnames()
        assert any(n.endswith("pyproject.toml") for n in names)


class TestMailArchiveTTL:
    def test_archive_respects_older_than(self, run, home):
        # recent mail stays
        run("mail", "send", "--to", "other", "--subject", "recent", "body")
        # old mail archived in profile's own inbox
        old = home / "mail" / "tester" / "inbox" / "old2.md"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(
            "---\nid: old2\ntype: mail\nauthor: other\nto: tester\naccess: L2\ncreated_at: 2026-01-01T00:00:00Z\nsubject: old\nmsg_status: read\npriority: fyi\n---\nbody\n"
        )
        code, out, _ = run("mail", "archive", "--older-than", "30")
        assert out["data"]["archived"] == 1


class TestLockList:
    def test_list_shows_active_and_expired(self, run):
        run("lock", "set", "--target", "a", "--reason", "active", "--expire", "10m")
        run("lock", "set", "--target", "b", "--reason", "expired", "--expire=-10m")
        code, out, _ = run("lock", "list")
        assert code == 0
        assert len(out["data"]["active"]) == 1
        assert len(out["data"]["expired"]) == 1


class TestAlertLifecycle:
    def test_alert_set_and_list(self, run):
        run("alert", "set", "--target", "svc", "--severity", "warn", "--active-until", "10m", "cpu high")
        code, out, _ = run("alert", "list")
        assert code == 0
        assert len(out["data"]["active"]) == 1

    def test_alert_invalid_severity(self, run):
        code, out, _ = run("alert", "set", "--target", "svc", "--severity", "fatal", "--active-until", "10m")
        assert code == 3


class TestUserKinds:
    def test_user_kind_decision(self, run, home):
        run("user", "post", "need decision", "--kind", "decision-needed")
        files = list((home / "user-feed").glob("*.md"))
        assert "decision-needed" in files[0].read_text(encoding="utf-8")


class TestSearch:
    def test_search_l2_visible(self, run):
        run("log", "visible log entry")
        run("index", "--rebuild")
        code, out, _ = run("search", "visible")
        assert code == 0
        assert out["data"]["count"] >= 1

    def test_search_respects_l1(self, run, other):
        run("think", "secret gamma")
        run("index", "--rebuild")
        code, out, _ = run("search", "gamma", profile_override=other)
        assert code == 0
        assert out["data"]["count"] == 0


class TestDoctorIssues:
    def test_doctor_reports_expired_lock(self, run):
        run("lock", "set", "--target", "x", "--reason", "r", "--expire=-1m")
        code, out, _ = run("doctor")
        assert any(i["type"] == "expired_locks" for i in out["data"]["issues"])

    def test_doctor_reports_expired_alert(self, run):
        run("alert", "set", "--target", "x", "--severity", "info", "--active-until=-1m", "old")
        code, out, _ = run("doctor")
        assert any(i["type"] == "expired_alerts" for i in out["data"]["issues"])

    def test_doctor_reports_sizes(self, run):
        code, out, _ = run("doctor")
        assert "sizes" in out["data"]


class TestPromote:
    def test_promote_creates_new_with_ref(self, run, home, profile):
        run("think", "original")
        original = list((home / "thoughts" / profile / "l1").glob("*.md"))[0]
        text = original.read_text(encoding="utf-8")
        original_id = [l for l in text.splitlines() if l.startswith("id:")][0].split(":", 1)[1].strip()
        run("user", "post", f"promoted ref:{original_id}", "--kind", "fyi")
        posts = list((home / "user-feed").glob("*.md"))
        assert len(posts) == 1
        assert original.read_text(encoding="utf-8") == text


class TestEnvelope:
    def test_envelope_has_required_keys(self, run):
        code, out, _ = run("think", "x")
        assert set(out.keys()) == {"data", "metadata", "ok", "reason", "status"}
        assert "version" in out["metadata"]
        assert "elapsed_ms" in out["metadata"]


class TestAccessDefaultByType:
    def test_think_default_l1(self, run, home, profile):
        run("think", "x")
        assert "access: L1" in list((home / "thoughts" / profile / "l1").glob("*.md"))[0].read_text(encoding="utf-8")

    def test_log_default_l2(self, run, home):
        run("log", "x")
        files = list((home / "journal").rglob("*.md"))
        assert "access: L2" in files[0].read_text(encoding="utf-8")

    def test_mail_default_l2(self, run, home, other):
        run("mail", "send", "--to", other, "--subject", "s")
        files = list((home / "mail" / other / "inbox").glob("*.md"))
        assert "access: L2" in files[0].read_text(encoding="utf-8")

    def test_user_default_l3(self, run, home):
        run("user", "post", "x")
        files = list((home / "user-feed").glob("*.md"))
        assert "access: L3" in files[0].read_text(encoding="utf-8")

    def test_explicit_access_overrides(self, run, home):
        run("log", "x", "--access", "L3")
        files = list((home / "journal").rglob("*.md"))
        assert "access: L3" in files[0].read_text(encoding="utf-8")


class TestNegativePaths:
    def test_invalid_date_format(self, run):
        code, out, _ = run("log", "x", "--deadline", "not-a-date")
        assert code != 0

    def test_empty_thought(self, run):
        code, out, _ = run("think", "")
        assert code == 3

    def test_missing_required_handoff(self, run):
        code, out, _ = run("handoff", "--to", "other")
        assert code == 3

    def test_invalid_expire(self, run):
        code, out, _ = run("lock", "set", "--target", "x", "--expire", "notime")
        assert code == 3


class TestAuditLog:
    def test_audit_created(self, run, home):
        run("think", "audit secret")
        run("index", "--rebuild")
        run("search", "secret")  # triggers L1 audit event
        assert (home / ".state" / "audit.log").exists()


class TestEntryPoint:
    def test_main_returns_int(self, monkeypatch):
        monkeypatch.setenv("A2A_HOME", "/tmp/a2a-test-home")
        monkeypatch.setenv("A2A_PROFILE", "tester")
        with pytest.raises(SystemExit) as exc:
            cli.main(["--help"])
        assert exc.value.code == 0


class TestDoctorJournalStatus:
    def test_doctor_status_ok_when_healthy(self, run):
        code, out, _ = run("doctor")
        assert out["data"]["status"] == "ok"

    def test_doctor_status_degraded_when_orphan(self, run, home):
        orphan = home / "journal" / "2026" / "09" / "orphan2.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("not a valid front matter record\n")
        code, out, _ = run("doctor")
        assert out["data"]["status"] == "degraded"


class TestReadUnread:
    def test_read_unread_filter(self, run, other):
        run("mail", "send", "--to", other, "--subject", "u")
        run("mail", "send", "--to", other, "--subject", "r")
        code, out, _ = run("mail", "list", "--inbox", profile_override=other)
        mid = out["data"]["entries"][0]["id"]
        run("mail", "read", mid, profile_override=other)
        # list unread only
        code2, out2, _ = run("mail", "list", "--inbox", "--unread", profile_override=other)
        assert code2 == 0
        assert out2["data"]["count"] == 1


class TestLockReason:
    def test_lock_reason_preserved(self, run, home):
        run("lock", "set", "--target", "svc", "--reason", "maintenance", "--expire", "5m")
        files = list((home / "locks").glob("*.md"))
        assert "maintenance" in files[0].read_text(encoding="utf-8")


class TestStatusUntil:
    def test_status_until_preserved(self, run, home):
        run("status", "set", "--state", "busy", "--until", "2026-09-26T09:00:00Z")
        path = home / "status" / "tester.md"
        assert "2026-09-26T09:00:00Z" in path.read_text(encoding="utf-8")


class TestHandoffPayload:
    def test_handoff_payload_preserved(self, run, home):
        run("handoff", "--to", "other", "--task", "t", "--acceptance", "a", "--payload", "/tmp/x")
        files = list((home / "records" / "handoffs").rglob("*.md"))
        assert "/tmp/x" in files[0].read_text(encoding="utf-8")


class TestUserAccess:
    def test_user_default_access_l3(self, run, home):
        run("user", "post", "x")
        files = list((home / "user-feed").glob("*.md"))
        assert "access: L3" in files[0].read_text(encoding="utf-8")


class TestMailSubject:
    def test_mail_subject_preserved(self, run, home, other):
        run("mail", "send", "--to", other, "--subject", "Specific Subject", "body")
        files = list((home / "mail" / other / "inbox").glob("*.md"))
        assert "Specific Subject" in files[0].read_text(encoding="utf-8")


class TestJournalWeek:
    def test_journal_week_no_filter_today(self, run):
        run("log", "week entry")
        code, out, _ = run("journal", "--week")
        assert code == 0


class TestVerifyEmpty:
    def test_verify_empty_storage(self, run):
        code, out, _ = run("verify")
        assert code == 0
        assert out["data"]["verified"] is True


class TestIndexRebuildEmpty:
    def test_rebuild_empty_counts_zero(self, run):
        code, out, _ = run("index", "--rebuild")
        assert code == 0
        assert out["data"]["counts"] == {}


class TestL1SnippetHidden:
    def test_l1_snippet_not_exposed_to_other(self, run, other):
        run("think", "private zzz")
        run("index", "--rebuild")
        code, out, _ = run("search", "zzz", profile_override=other)
        for r in out["data"]["results"]:
            assert "zzz" not in r.get("snippet", "")


class TestReadL1Owner:
    def test_owner_can_read_l1(self, run):
        run("think", "owner only")
        run("index", "--rebuild")
        code, out, _ = run("read")
        assert any("owner only" in e.get("snippet", "") for e in out["data"]["entries"])


class TestNoL1FromOtherInRead:
    def test_read_hides_l1_from_others(self, run, other):
        run("think", "hidden")
        run("index", "--rebuild")
        code, out, _ = run("read", profile_override=other)
        assert not any("hidden" in e.get("snippet", "") for e in out["data"]["entries"])


class TestStatusDNDSet:
    def test_dnd_set_creates_status(self, run, home):
        run("dnd", "set", "--until", "2099-12-31T23:59:59Z")
        path = home / "status" / "tester.md"
        assert path.exists()
        assert "state: dnd" in path.read_text(encoding="utf-8")


class TestSearchRebuilds:
    def test_search_rebuilds_index(self, run):
        run("think", "search me")
        code, out, _ = run("search", "search me")
        assert code == 0
        assert out["data"]["count"] == 1


class TestAuditFixes:
    """Regression tests for AUDIT-REPORT.md C1-C6 and M1-M7."""

    def test_fix_C1_read_logs_l1_access(self, run, home, profile, other):
        run("think", "private alpha")
        run("index", "--rebuild")
        run("read", profile_override=other)
        audit_path = home / ".state" / "audit.log"
        assert audit_path.exists()
        events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").strip().splitlines()]
        assert any(e["event"] == "read_l1" and e["caller"] == other for e in events)

    def test_fix_C2_feed_hides_l1_from_others(self, run, profile, other):
        run("user", "post", "secret feed", "--access", "L1")
        run("index", "--rebuild")
        code, out, _ = run("feed", profile_override=other)
        assert code == 0
        assert out["data"]["count"] == 0
        code2, out2, _ = run("feed")
        assert code2 == 0
        assert any("secret feed" in e.get("snippet", "") for e in out2["data"]["entries"])

    def test_fix_C3_invalid_access_fail_closed(self, run, home, profile):
        run("think", "tampered")
        files = list((home / "thoughts" / profile / "l1").glob("*.md"))
        text = files[0].read_text(encoding="utf-8")
        files[0].write_text(text.replace("access: L1", "access: INVALID"), encoding="utf-8")
        run("index", "--rebuild")
        code, out, _ = run("read")
        assert code == 0
        assert not any("tampered" in e.get("snippet", "") for e in out["data"]["entries"])
        code2, out2, _ = run("verify")
        assert code2 == 3
        assert any("access" in issue.get("reason", "") or "invalid" in issue.get("reason", "")
                   for issue in out2["data"]["issues"])

    def test_fix_C4_doctor_degraded_exit_2(self, run, home):
        orphan = home / "journal" / "2026" / "09" / "orphan_doctor.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("not a valid front matter record\n")
        code, out, _ = run("doctor")
        assert code == 2
        assert out["data"]["status"] == "degraded"
        assert any(i["type"] == "orphan_files" for i in out["data"]["issues"])

    def test_fix_C5_thought_too_long(self, run):
        long_text = "x" * 2001
        code, out, _ = run("think", long_text)
        assert code == 3
        assert out["reason"] == "thought_too_long"

    def test_fix_C6_new_record_independent_but_inplace_detected(self, run, home, profile):
        # new L1 after L2 is allowed (append-only, independent records)
        run("log", "public", "--access", "L2")
        code, out, _ = run("log", "private", "--access", "L1")
        assert code == 0
        # in-place downgrade: file stored in l2 dir but declares access L1
        bad_path = home / "thoughts" / profile / "l2" / "fake.md"
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_text(
            "---\nid: fake1\ntype: thought\nauthor: tester\naccess: L1\ncreated_at: 2026-09-25T00:00:00Z\ntopic: general\n---\nbody\n",
            encoding="utf-8",
        )
        code2, out2, _ = run("verify")
        assert code2 == 3
        assert any(issue.get("reason") == "in_place_access_mismatch" for issue in out2["data"]["issues"])

    def test_fix_M1_invalid_access_journal_hidden(self, run, home, profile):
        bad = home / "journal" / "2026" / "09" / "bad_access.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text(
            "---\nid: bad-j\ntype: journal\nauthor: tester\naccess: L0\ncreated_at: 2026-09-25T00:00:00Z\nwhat_done: x\n---\nbody\n",
            encoding="utf-8",
        )
        code, out, _ = run("journal", "--today", "--all")
        assert code == 0
        assert not any(e["id"] == "bad-j" for e in out["data"]["entries"])

    def test_fix_M2_fts_syntax_error_exit_4(self, run):
        code, out, _ = run("search", "ref:mail")
        assert code == 4
        assert "search_failed" in out["reason"]

    def test_fix_M4_handoff_inbox_is_reference(self, run, home, profile, other):
        run("handoff", "--to", other, "--task", "migrate db", "--acceptance", "tests green")
        handoff_files = list((home / "records" / "handoffs").glob("*.md"))
        inbox_files = list((home / "mail" / other / "inbox").glob("*.md"))
        assert len(handoff_files) == 1
        assert len(inbox_files) == 1
        ref_text = inbox_files[0].read_text(encoding="utf-8")
        assert "ref_type: handoff" in ref_text
        assert "ref: " in ref_text
        assert ref_text != handoff_files[0].read_text(encoding="utf-8")

    def test_fix_M5_mail_check_wrapper(self, run, profile, other, home):
        run("mail", "send", "--to", other, "--subject", "check me", "body")
        before = {p: p.stat().st_mtime_ns for p in (home / "journal").rglob("*.md")}
        code, out, _ = run("mail", "check", "--include-body", profile_override=other)
        assert code == 0
        assert out["data"]["count"] == 1
        mid = out["data"]["entries"][0]["id"]
        assert out["data"]["entries"][0]["subject"] == "check me"
        assert out["data"]["entries"][0]["body"] == "body\n"
        assert (home / "mail" / other / "inbox").glob(f"*{mid}.md")
        assert all("msg_status: unread" in p.read_text(encoding="utf-8-sig") for p in (home / "mail" / other / "inbox").glob(f"*{mid}.md"))
        after = {p: p.stat().st_mtime_ns for p in (home / "journal").rglob("*.md")}
        assert after == before, "mail check must not append doctor/journal proof records"
        run("mail", "read", mid, profile_override=other)
        code2, out2, _ = run("mail", "check", profile_override=other)
        assert code2 == 0
        assert out2["data"]["count"] == 0
        assert {p: p.stat().st_mtime_ns for p in (home / "journal").rglob("*.md")} == before

    def test_mail_check_reports_errors_without_writing_journal(self, run, home, other):
        bad = home / "mail" / other / "inbox" / "broken.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("not a record", encoding="utf-8")
        before = {p: p.stat().st_mtime_ns for p in (home / "journal").rglob("*.md")}
        code, out, _ = run("mail", "check", profile_override=other)
        assert code == 0
        assert out["data"]["count"] == 0
        assert {p: p.stat().st_mtime_ns for p in (home / "journal").rglob("*.md")} == before

    def test_fix_M6_doctor_git_uncommitted(self, run, home):
        subprocess.run(["git", "init", "-q", str(home)], check=True)
        (home / "uncommitted.txt").write_text("x", encoding="utf-8")
        code, out, _ = run("doctor")
        assert code == 2
        assert any(i["type"] == "git_uncommitted_changes" for i in out["data"]["issues"])

    def test_fix_M7_doctor_detects_sqlite_drift(self, run, home, profile):
        # v0.2.2: sqlite_drift понижен до drift_info (нормальная активность
        # между прогонами). Удаление файла после rebuild ловится orphan-
        # проверкой как journal_orphan_missing.
        run("log", "will vanish")
        run("index", "--rebuild")
        files = list((home / "journal").rglob("*.md"))
        for f in files:
            f.unlink()
        code, out, _ = run("doctor")
        assert code == 2
        assert any(
            i["type"] in ("index_ghost_entries", "orphan_files")
            for i in out["data"]["issues"]
        ), out["data"]["issues"]
        # дрейф счётчиков больше не деградация
        assert not any(i["type"] == "sqlite_drift" for i in out["data"]["issues"])


class TestBomRecords:
    def test_fix_bom_records_parse(self, run, home, profile):
        """Records with UTF-8 BOM (project convention) must parse — coordinator found
        legacy v1 records unreadable because read_record used utf-8 without -sig."""
        rec_dir = home / "records" / "decisions"
        rec_dir.mkdir(parents=True, exist_ok=True)
        rec = rec_dir / "bom_record.md"
        rec.write_bytes(
            b"\xef\xbb\xbf---\nid: bom1\naccess: L2\ntype: journal\n"
            b"created_at: '2026-09-25T00:00:00Z'\nauthor: tester\nwhat_done: test entry\n"
            b"status: ok\nartifacts: []\n---\nbody text\n"
        )
        code, out, _ = run("verify")
        issues = out["data"].get("issues") or []
        flagged = [i for i in issues if "bom_record" in i["path"]]
        assert flagged == [], f"BOM record flagged: {flagged}"


class TestUserFeedMirror:
    def test_fix_feed_mirror_on_post(self, run, home, profile):
        """user post must mirror into A2A_USER_FEED (Note: user-feed)."""
        code, out, _ = run("user", "post", "mirror check", "--kind", "fyi")
        assert code == 0
        obs = out["data"].get("notesapp")
        assert obs is not None, "no notesapp mirror path in envelope"
        from pathlib import Path as _P
        raw = _P(obs).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), "mirror copy missing BOM"


class TestMailBareInvocation:
    def test_fix_bare_mail_lists_inbox(self, run, profile, other):
        """v0.2.3 regression: bare `a2a mail` and `a2a mail --outbox/--unread`
        crashed with AttributeError (flags only declared on `list` subparser)."""
        run("mail", "send", "--to", other, "--subject", "bare mail test", "body")
        code, out, _ = run("mail", profile_override=other)
        assert code == 0, out
        assert out["data"]["count"] == 1
        assert out["data"]["entries"][0]["subject"] == "bare mail test"

        code2, out2, _ = run("mail", "--unread", profile_override=other)
        assert code2 == 0
        assert out2["data"]["count"] == 1

        code3, out3, _ = run("mail", "--outbox", profile_override=other)
        assert code3 == 0
        assert out3["data"]["count"] == 0  # other sent nothing

        # after read, --unread no longer shows it
        mid = out["data"]["entries"][0]["id"]
        run("mail", "read", mid, profile_override=other)
        code4, out4, _ = run("mail", "--unread", profile_override=other)
        assert code4 == 0
        assert out4["data"]["count"] == 0


class TestLockClean:
    def test_lock_clean_removes_expired_only(self, run, home, profile, other):
        """v0.2.3 release gate: doctor reports expired locks; `lock clean` is the
        explicit sweep. Active locks must survive."""
        run("lock", "set", "--target", "keep-me", "--reason", "still active", "--expire", "30m", profile_override=other)
        run("lock", "set", "--target", "old-stuff", "--reason", "ancient", "--expire", "1m")
        old = home / "locks"
        import time as _t
        # manually age the second lock by rewriting its expires into the past
        for p in old.glob("*.md"):
            text = p.read_text(encoding="utf-8-sig")
            if "old-stuff" in text:
                text = text.replace("expires: '", "expires: '2026-01-01T00:00:00Z")
                # crude: replace whole line instead
                lines = text.splitlines()
                lines = [f"expires: '2026-01-01T00:00:00Z'" if ln.startswith("expires:") else ln for ln in lines]
                p.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

        code, out, _ = run("lock", "clean")
        assert code == 0
        assert out["data"]["removed"] == 1

        code2, out2, _ = run("lock", "list")
        assert code2 == 0
        assert len(out2["data"]["active"]) == 1
        assert out2["data"]["active"][0]["target"] == "keep-me"
        assert out2["data"]["expired"] == []


class TestProfileNameValidation:
    """F-01: profile/addressee names must not allow path traversal."""

    @pytest.mark.parametrize(
        "name",
        ["", " ", "a b", "../../../evil", "..", "a/b", "a\\b", "profile\x00x", "a" * 65],
    )
    def test_validate_profile_name_rejects_unsafe(self, name):
        from a2a.util import validate_profile_name
        assert validate_profile_name(name) is False

    @pytest.mark.parametrize("name", ["a", "A", "tester", "coordinator", "under_score", "0xdeadbeef"])
    def test_validate_profile_name_accepts_safe(self, name):
        from a2a.util import validate_profile_name
        assert validate_profile_name(name) is True

    def test_cli_rejects_invalid_profile_flag(self, run):
        code, out, _ = run("--profile", "../../../evil", "think", "x")
        assert code == 4
        assert out["reason"] == "invalid_profile_name"

    def test_resolve_profile_rejects_invalid_as_flag(self, monkeypatch):
        from argparse import Namespace
        from a2a.util import resolve_profile

        monkeypatch.delenv("A2A_PROFILE", raising=False)
        monkeypatch.delenv("HERMES_PROFILE", raising=False)
        with pytest.raises(SystemExit) as exc:
            resolve_profile(Namespace(profile=None, as_="bad name"))
        assert exc.value.code == 4

    def test_cli_rejects_invalid_env_profile(self, run):
        code, out, _ = run("think", "x", profile_override="../../attacker")
        assert code == 4
        assert out["reason"] == "invalid_profile_name"

    def test_mail_send_rejects_invalid_to(self, run):
        code, out, _ = run("mail", "send", "--to", "../../evil", "--subject", "x")
        assert code == 4
        assert out["reason"] == "invalid_profile_name"


class TestLockMutex:
    """F-02: lock creation must use an OS-level file mutex."""

    def test_lock_mutex_created_and_released(self, run, home):
        import fcntl

        run("lock", "set", "--target", "svc", "--reason", "r", "--expire", "5m")
        mutex = home / "locks" / ".lock_mutex"
        assert mutex.exists()
        with open(mutex, "a+") as fh:
            # If the mutex was leaked, this would block or raise BlockingIOError.
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def test_lock_conflict_still_detected_with_mutex(self, run, profile, other):
        run("lock", "set", "--target", "shared", "--reason", "first", "--expire", "10m", profile_override=other)
        code, out, _ = run("lock", "set", "--target", "shared", "--reason", "second", "--expire", "10m")
        assert code == 1
        assert out["reason"] == "lock_conflict"


class TestMailCheckRace:
    """F-03: mail check --include-body must survive a concurrent deletion."""

    def test_mail_check_skips_body_when_record_disappears(self, monkeypatch, tmp_path):
        import fcntl
        from argparse import Namespace
        from a2a import mail, util

        home = tmp_path / "shared_workspace"
        util.HOME = home
        inbox = home / "mail" / "other" / "inbox"
        inbox.mkdir(parents=True)
        inbox.joinpath("2026-10-11_msg.md").write_text(
            "---\nid: msg\ntype: mail\nauthor: tester\nto: other\naccess: L2\n"
            "created_at: '2026-10-11T00:00:00Z'\nsubject: s\nmsg_status: unread\npriority: fyi\n---\nbody\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(mail, "_find_mail", lambda profile, id_: (None, None))
        args = Namespace(profile="other", include_body=True)
        with pytest.raises(SystemExit) as exc:
            mail.cmd_mail_check(args)
        assert exc.value.code == 0


class TestP02RedactAndWrite:
    """P0-2: journal — redact-and-write (запись сохраняется, секрет вырезан);
    PEM/url-секреты и режим reject — fail-closed reject."""

    def _journal_files(self, home):
        return list((home / "journal").rglob("*.md"))

    def test_journal_redacts_token_and_writes(self, run, home, profile):
        fake = "sk-" + "T" * 24
        code, out, _ = run("log", f"deploy complete, key={fake}")
        assert code == 0, out
        assert "api_key_sk" in out["data"].get("scrubbed", "")
        files = self._journal_files(home)
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "[REDACTED:API_KEY_SK]" in text
        assert fake not in text
        assert "scrubbed: api_key_sk" in text  # отметка в front-matter

    def test_pem_key_hard_rejected(self, run, home):
        code, out, _ = run("log", "leak: -----BEGIN RSA PRIVATE KEY----- body")
        assert code == 1
        assert out["reason"] == "secret_detected"
        assert "pem_private_key" in out["data"]["hits"]
        assert self._journal_files(home) == []

    def test_url_secret_hard_rejected(self, run, home):
        code, out, _ = run("log", "see https://api.site/x?token=abc123def456 now")
        assert code == 1
        assert out["reason"] == "secret_detected"
        assert self._journal_files(home) == []

    def test_reject_mode_restores_old_behavior(self, run, home):
        fake = "ghp_" + "X" * 36
        code, out, _ = run("log", f"tok {fake}", env_extra={"A2A_SCRUB_MODE": "reject"})
        assert code == 1
        assert out["reason"] == "secret_detected"
        assert self._journal_files(home) == []

    def test_mail_still_hard_rejects(self, run, home):
        fake = "sk-" + "M" * 24
        code, out, _ = run("mail", "send", "--to", "other", "--subject", "s",
                           "--body", f"secret {fake}")
        assert code == 1
        assert out["reason"] == "secret_detected"


class TestP25ReviewFindings:
    """Закрытие находок независимого ревью QUARANTINE (2026-10-20)."""

    def test_verify_output_never_echoes_credentials(self, run, home):
        # finding#1 HIGH: вывод --verify-run не должен попадать в stdout ни сырым, ни в файл
        fake = "AKIA" + "A" * 16
        code, out, err = run("log", "clean text", "--verify", f"echo {fake}", "--verify-run")
        assert code == 0
        assert fake not in json.dumps(out) + err, "verify_output эхо́мит секрет в stdout"
        files = list((home / "journal").rglob("*.md"))
        assert files and fake not in files[0].read_text(encoding="utf-8"), \
            "verify_output персистит секрет в запись журнала"
        assert "[REDACTED:AWS_KEY]" in json.dumps(out["data"]["verify_output"])

    def test_pgp_private_key_block_rejected(self, run, home):
        # finding#2 HIGH: PGP BLOCK-маркер обязан попадать в UNREDACTABLE
        code, out, _ = run("log", "x -----BEGIN PGP PRIVATE KEY BLOCK-----\nlADalal\n")
        assert code == 1
        assert "pgp_private_block" in out["data"]["hits"] or "pem_private_key" in out["data"]["hits"]
        assert list((home / "journal").rglob("*.md")) == []

    def test_new_patterns_detected(self, run, home):
        cases = {
            "glpat-" + "Z" * 22: "gitlab_pat",
            "https://hooks.slack.com/services/T00000/B00000/XXXXXXXXXXXX": "slack_webhook",
            "ya29." + "a" * 30: "gcp_oauth",
            "xapp-" + "1" * 25: "x_app_token",
            "https://admin:***@internal.site/x": "url_basic_auth",
        }
        for payload, name in cases.items():
            code, out, _ = run("log", f"note {payload}")
            # либо вырезано при записи, либо отклонено — но исходник не светится
            assert code in (0, 1)
            if code == 0:
                files = list((home / "journal").rglob("*.md"))
                assert payload not in files[-1].read_text(encoding="utf-8")
                assert name in out["data"].get("scrubbed", "")

    def test_unknown_scrub_mode_fails_closed(self, run, home):
        fake = "sk-" + "Q" * 24
        code, out, _ = run("log", f"tok {fake}",
                           env_extra={"A2A_SCRUB_MODE": "typo-mode"})
        assert code == 1  # неизвестный режим = reject, не "красить как попало"
        assert list((home / "journal").rglob("*.md")) == []


class TestP18SessionSourcing:
    def _files(self, home):
        return list((home / "journal" / "sessions").rglob("*.jsonl"))

    def test_full_cycle_seq_monotonic(self, run, home):
        code, out, _ = run("session", "start", "--session-id", "s1", "--note", "boot")
        assert code == 0
        run("session", "event", "--session-id", "s1", "--type", "tool_call", "--data", "ls -la")
        code, out, _ = run("session", "end", "--session-id", "s1")
        assert code == 0
        files = self._files(home)
        assert len(files) == 1
        events = [json.loads(l) for l in files[0].read_text(encoding="utf-8").splitlines()]
        assert [e["seq"] for e in events] == [1, 2, 3]
        assert [e["type"] for e in events] == ["session_start", "tool_call", "session_end"]

    def test_replay_check_pass(self, run, home):
        run("session", "start", "--session-id", "s2")
        run("session", "event", "--session-id", "s2", "--type", "decision", "--data", "go")
        f = self._files(home)[0]
        code, out, _ = run("replay", str(f), "--check")
        assert code == 0
        assert out["data"]["check"] == "PASS"

    def test_corrupt_line_detected(self, run, home):
        run("session", "start", "--session-id", "s3")
        f = self._files(home)[0]
        f.write_text(f.read_text(encoding="utf-8") + "{not-json\n", encoding="utf-8")
        code, out, _ = run("replay", str(f), "--check", check=False)
        assert code == 1

    def test_event_data_scrubbed_not_persisted(self, run, home):
        fake = "sk-" + "R" * 24
        run("session", "start", "--session-id", "s4")
        run("session", "event", "--session-id", "s4", "--type", "tool_result",
            "--data", f"output contains {fake}")
        f = self._files(home)[0]
        text = f.read_text(encoding="utf-8")
        assert fake not in text and "[REDACTED:API_KEY_SK]" in text

    def test_wrap_records_call_and_result(self, run, home):
        code, out, _ = run("session", "start", "--session-id", "s5")
        code, out, _ = run("session", "wrap", "--session-id", "s5",
                           "--cmd", "echo hello-wrap", check=False)
        assert code == 0 and out["data"]["rc"] == 0
        events = [json.loads(l) for l in self._files(home)[0].read_text(encoding="utf-8").splitlines()]
        types = [e["type"] for e in events]
        assert "tool_call" in types and "tool_result" in types
        result = [e for e in events if e["type"] == "tool_result"][0]
        assert "hello-wrap" in result["data"]

    def test_kill9_leaves_log_consistent(self, run, home):
        """P1-8 приёмка: SIGKILL пишущего процесса посреди серии — лог читается
        целиком до обрыва, seq не рвётся, полустёртой строки нет."""
        import sys, time as _t
        env = {**os.environ, "A2A_HOME": str(home), "A2A_PROFILE": "tester"}
        script = (
            "import os, time, sys\n"
            "sys.path.insert(0, os.getcwd())\n"
            "from a2a import sessions\n"
            "for i in range(40):\n"
            "    sessions.emit('tester', 's6', 'model_msg', f'tick {i}')\n"
            "    time.sleep(0.01)\n"
        )
        child = subprocess.Popen([sys.executable, "-c", script], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _t.sleep(0.15)
        child.kill()
        child.wait()
        f = [x for x in (home / "journal" / "sessions").rglob("*s6*.jsonl")]
        assert f, "лог не создавался"
        lines = f[0].read_text(encoding="utf-8").splitlines()
        assert lines, "после kill -9 лог пуст"
        events = [json.loads(l) for l in lines]  # каждая уцелевшая строка — валидный JSON
        seqs = [e["seq"] for e in events]
        assert seqs == list(range(1, len(seqs) + 1)), "seq оборван/дублирован"
        # replay --check не должен падать на этом файле... нет session_start -> ожидаемо fail,
        # поэтому проверяем низкоуровневую целостность отдельно и положительный кейс ниже
        run("session", "start", "--session-id", "s7")
        g = [x for x in (home / "journal" / "sessions").rglob("*s7*.jsonl")][0]
        code, out, _ = run("replay", str(g), "--check")
        assert code == 0 and out["data"]["check"] == "PASS"


def test_mail_wake_default_by_priority(run, home, profile, other):
    """v0.4.5: today-почта будит адресата сама, fyi — тихое, --no-wake глушит."""
    run("status", "set", "--state", "idle", profile_override=other)
    # today -> wake triggered
    code, out, _ = run("mail", "send", "--to", other, "--priority", "today",
                       "--subject", "s", "--body", "b", profile_override=profile)
    assert code == 0 and out["data"]["wake"]["triggered"] is True
    # fyi -> тихое
    code, out, _ = run("mail", "send", "--to", other, "--priority", "fyi",
                       "--subject", "s", "--body", "b", profile_override=profile)
    assert code == 0 and "wake" not in out["data"]
    # today + явный --no-wake -> тихое
    code, out, _ = run("mail", "send", "--to", other, "--priority", "today",
                       "--no-wake", "--subject", "s", "--body", "b",
                       profile_override=profile)
    assert code == 0 and "wake" not in out["data"]


def test_user_feed_rejects_secret(run, home):
    """P0-2 residual: витрина для User — fail-closed reject, зеркало не создаётся."""
    fake = "sk-" + "V" * 24
    code, out, _ = run("user", "post", f"public note {fake}", check=False)
    assert code == 1
    assert out["reason"] == "secret_detected"
    mirror = home.parent / "notesapp-mirror"
    assert not mirror.exists() or list(mirror.glob("*.md")) == []


