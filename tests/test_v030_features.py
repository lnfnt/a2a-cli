"""Tests for v0.3.0 SPEC features."""

from __future__ import annotations

import json
from pathlib import Path


class TestMailSendBody:
    def test_mail_send_body_flag(self, run, home, other):
        code, out, _ = run("mail", "send", "--to", other, "--subject", "flag body", "--body", "flagged text")
        assert code == 0
        files = list((home / "mail" / other / "inbox").glob("*.md"))
        assert len(files) == 1
        assert "flagged text" in files[0].read_text(encoding="utf-8")

    def test_mail_send_body_positional(self, run, home, other):
        code, out, _ = run("mail", "send", "--to", other, "--subject", "positional body", "positional text")
        assert code == 0
        files = list((home / "mail" / other / "inbox").glob("*.md"))
        assert len(files) == 1
        assert "positional text" in files[0].read_text(encoding="utf-8")

    def test_mail_send_body_stdin(self, run, home, other):
        code, out, _ = run("mail", "send", "--to", other, "--subject", "stdin body", stdin="stdin text")
        assert code == 0
        files = list((home / "mail" / other / "inbox").glob("*.md"))
        assert len(files) == 1
        assert "stdin text" in files[0].read_text(encoding="utf-8")

    def test_mail_send_body_flag_beats_positional(self, run, home, other):
        code, out, _ = run("mail", "send", "--to", other, "--subject", "priority", "--body", "flag", "positional")
        assert code == 0
        files = list((home / "mail" / other / "inbox").glob("*.md"))
        text = files[0].read_text(encoding="utf-8")
        assert "flag" in text


class TestMailMarkRead:
    def test_mail_mark_read_changes_status(self, run, profile, other):
        run("mail", "send", "--to", other, "--subject", "mark read", "body")
        code, out, _ = run("mail", "list", "--inbox", profile_override=other)
        assert code == 0
        mid = out["data"]["entries"][0]["id"]
        code2, out2, _ = run("mail", "mark-read", mid, profile_override=other)
        assert code2 == 0
        assert out2["data"]["msg_status"] == "read"
        assert "body" not in out2["data"]
        code3, out3, _ = run("mail", "list", "--inbox", "--unread", profile_override=other)
        assert code3 == 0
        assert out3["data"]["count"] == 0


class TestDoctorRunAlias:
    def test_doctor_run_equivalent(self, run, home):
        code, out, _ = run("doctor", "run")
        assert code == 0
        assert out["data"]["status"] == "ok"
        files = list((home / "journal").rglob("*.md"))
        assert any("a2a doctor run" in f.read_text(encoding="utf-8") for f in files)


class TestLogVerifyRun:
    def test_log_verify_run_success(self, run):
        code, out, _ = run("log", "verified task", "--verify", "true", "--verify-run")
        assert code == 0
        assert out["data"]["status"] == "verified"
        assert out["data"]["verify_exit_code"] == 0

    def test_log_verify_run_failure(self, run):
        code, out, _ = run("log", "unverified task", "--verify", "sh -c 'echo failed; false'", "--verify-run")
        assert code == 0
        assert out["data"]["status"] == "VERIFY_FAILED"
        assert out["data"]["verify_exit_code"] == 1
        assert "failed" in out["data"]["verify_output"]


class TestMailPrune:
    def test_prune_dry_run_counts_old_read_mail(self, run, home):
        old = home / "mail" / "tester" / "inbox" / "old_prune.md"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(
            "---\nid: old-prune\ntype: mail\nauthor: other\nto: tester\naccess: L2\n"
            "created_at: 2026-01-01T00:00:00Z\nsubject: old\nmsg_status: read\npriority: fyi\n---\nbody\n"
        )
        code, out, _ = run("mail", "prune", "--dry-run")
        assert code == 0
        assert out["data"]["pruned"] == 1
        assert out["data"]["dry_run"] is True
        assert old.exists()

    def test_prune_removes_old_read_mail(self, run, home):
        old = home / "mail" / "tester" / "inbox" / "old_prune2.md"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(
            "---\nid: old-prune2\ntype: mail\nauthor: other\nto: tester\naccess: L2\n"
            "created_at: 2026-01-01T00:00:00Z\nsubject: old\nmsg_status: read\npriority: fyi\n---\nbody\n"
        )
        code, out, _ = run("mail", "prune")
        assert code == 0
        assert out["data"]["pruned"] == 1
        assert not old.exists()

    def test_prune_respects_days(self, run, home):
        from datetime import datetime, timedelta, timezone

        # v0.4.1: относительная дата — фиксированная 2026-10-01 сделала тест
        # «время-бомбой»: 19.10.2026 письмо уже старше 15 дней и тест падал.
        five_days_ago = datetime.now(timezone.utc) - timedelta(days=5)
        stamp = five_days_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
        recent = home / "mail" / "tester" / "inbox" / "recent_prune.md"
        recent.parent.mkdir(parents=True, exist_ok=True)
        recent.write_text(
            "---\nid: recent-prune\ntype: mail\nauthor: other\nto: tester\naccess: L2\n"
            f"created_at: {stamp}\nsubject: recent\nmsg_status: read\npriority: fyi\n---\nbody\n"
        )
        code, out, _ = run("mail", "prune", "--days", "15")
        assert code == 0
        assert out["data"]["pruned"] == 0
        assert recent.exists()

    def test_prune_skips_unread(self, run, home):
        unread = home / "mail" / "tester" / "inbox" / "unread_prune.md"
        unread.parent.mkdir(parents=True, exist_ok=True)
        unread.write_text(
            "---\nid: unread-prune\ntype: mail\nauthor: other\nto: tester\naccess: L2\n"
            "created_at: 2026-01-01T00:00:00Z\nsubject: unread\nmsg_status: unread\npriority: fyi\n---\nbody\n"
        )
        code, out, _ = run("mail", "prune")
        assert code == 0
        assert out["data"]["pruned"] == 0
        assert unread.exists()


class TestSearchFilters:
    def test_search_filter_author(self, run, other):
        run("log", "alpha unique")
        run("log", "beta other", profile_override=other)
        run("index", "--rebuild")
        code, out, _ = run("search", "alpha", "--author", "tester")
        assert code == 0
        assert out["data"]["count"] == 1
        assert out["data"]["results"][0]["author"] == "tester"

    def test_search_filter_type(self, run, other):
        run("log", "journal entry")
        run("mail", "send", "--to", other, "--subject", "mail subject", "mail body")
        run("index", "--rebuild")
        code, out, _ = run("search", "entry", "--type", "journal")
        assert code == 0
        assert all(r["type"] == "journal" for r in out["data"]["results"])
        code2, out2, _ = run("search", "mail", "--type", "mail")
        assert code2 == 0
        assert all(r["type"] == "mail" for r in out2["data"]["results"])

    def test_search_filter_since(self, run):
        run("log", "recent search entry")
        run("index", "--rebuild")
        code, out, _ = run("search", "recent", "--since", "2026-10-01")
        assert code == 0
        assert out["data"]["count"] >= 1
        code2, out2, _ = run("search", "recent", "--since", "2099-01-01")
        assert code2 == 0
        assert out2["data"]["count"] == 0

    def test_search_invalid_since(self, run):
        code, out, _ = run("search", "x", "--since", "not-a-date")
        assert code == 4
        assert "invalid_since_date" in out["reason"]

    def test_search_hyphenated_term(self, run):
        run("log", "sud-cli task")
        run("index", "--rebuild")
        code, out, _ = run("search", "sud-cli")
        assert code == 0
        assert out["data"]["count"] == 1
        assert "sud-cli" in out["data"]["results"][0]["snippet"]
