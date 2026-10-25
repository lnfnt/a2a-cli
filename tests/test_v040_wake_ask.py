"""Tests for v0.4.0 wake and ask features."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path


class TestMailSendWake:
    def test_wake_creates_marker_and_flag(self, run, home, other):
        code, out, _ = run("mail", "send", "--to", other, "--subject", "wake me", "--wake", "please wake")
        assert code == 0
        assert out["data"]["wake"]["triggered"] is True
        assert out["data"]["wake"]["target"] == other

        files = list((home / "mail" / other / "inbox").glob("*.md"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "wake: true" in text
        assert "depth: 1" in text

        marker = home / "wake" / f"{other}.wake"
        assert marker.exists()
        payload = json.loads(marker.read_text(encoding="utf-8"))
        assert payload["from"] == "tester"
        assert payload["mail_id"] == out["data"]["id"]
        assert "created_at" in payload

    def test_wake_marker_respects_timeout(self, run, home, other):
        run("mail", "send", "--to", other, "--subject", "timed wake", "--wake", "--timeout", "120", "body")
        marker = json.loads((home / "wake" / f"{other}.wake").read_text(encoding="utf-8"))
        created = datetime.fromisoformat(marker["created_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(marker["expires_at"].replace("Z", "+00:00"))
        assert (expires - created).total_seconds() == 120

    def test_wake_suppressed_by_dnd(self, run, home, other):
        run("status", "set", "--state", "dnd", "--until", "2099-12-31T23:59:59Z", profile_override=other)
        code, out, _ = run("mail", "send", "--to", other, "--subject", "dnd wake", "--wake", "body")
        assert code == 0
        assert out["data"]["wake"]["triggered"] is False
        assert out["data"]["wake"]["suppressed"] == "target_dnd"
        assert not (home / "wake" / f"{other}.wake").exists()

    def test_wake_suppressed_by_busy(self, run, home, other):
        run("status", "set", "--state", "busy", profile_override=other)
        code, out, _ = run("mail", "send", "--to", other, "--subject", "busy wake", "--wake", "body")
        assert code == 0
        assert out["data"]["wake"]["triggered"] is False
        assert out["data"]["wake"]["suppressed"] == "target_busy"
        assert not (home / "wake" / f"{other}.wake").exists()

    def test_wake_ignored_at_depth_two(self, run, home, other):
        code, out, _ = run("mail", "send", "--to", other, "--subject", "deep", "--wake", "--depth", "2", "body")
        assert code == 0
        assert out["data"]["wake"]["triggered"] is False
        assert out["data"]["wake"]["suppressed"] == "max_depth"
        assert not (home / "wake" / f"{other}.wake").exists()
        files = list((home / "mail" / other / "inbox").glob("*.md"))
        assert "wake:" not in files[0].read_text(encoding="utf-8")

    def test_no_wake_without_flag(self, run, home, other):
        run("mail", "send", "--to", other, "--subject", "no wake", "body")
        files = list((home / "mail" / other / "inbox").glob("*.md"))
        assert "wake:" not in files[0].read_text(encoding="utf-8")
        assert not (home / "wake" / f"{other}.wake").exists()


class TestSchemaWakeDepth:
    def test_wake_must_be_bool(self):
        from a2a import schema

        base = {
            "id": "x",
            "type": "mail",
            "author": "a",
            "access": "L2",
            "created_at": "2026-01-01T00:00:00Z",
            "to": "b",
            "subject": "s",
        }
        ok, reason = schema.validate({**base, "wake": "yes"}, "")
        assert not ok
        assert "wake must be bool" in reason

    def test_depth_must_be_int(self):
        from a2a import schema

        base = {
            "id": "x",
            "type": "mail",
            "author": "a",
            "access": "L2",
            "created_at": "2026-01-01T00:00:00Z",
            "to": "b",
            "subject": "s",
        }
        ok, reason = schema.validate({**base, "depth": "1"}, "")
        assert not ok
        assert "depth must be int" in reason

    def test_valid_wake_and_depth_pass(self):
        from a2a import schema

        base = {
            "id": "x",
            "type": "mail",
            "author": "a",
            "access": "L2",
            "created_at": "2026-01-01T00:00:00Z",
            "to": "b",
            "subject": "s",
        }
        ok, reason = schema.validate({**base, "wake": True, "depth": 1}, "")
        assert ok


class TestRpcSchemas:
    def test_valid_request(self):
        from a2a import schema

        req = {
            "id": "x",
            "from": "a",
            "to": "b",
            "question": "q",
            "timeout": 30,
            "created_at": "2026-01-01T00:00:00Z",
        }
        ok, reason = schema.validate_rpc_request(req)
        assert ok

    def test_invalid_timeout(self):
        from a2a import schema

        req = {
            "id": "x",
            "from": "a",
            "to": "b",
            "question": "q",
            "timeout": -1,
            "created_at": "2026-01-01T00:00:00Z",
        }
        ok, reason = schema.validate_rpc_request(req)
        assert not ok

    def test_valid_response(self):
        from a2a import schema

        resp = {"call_id": "x", "from": "b", "answer": "answer"}
        ok, reason = schema.validate_rpc_response(resp)
        assert ok


class TestAsk:
    def test_ask_success(self, run, home, other):
        result = {}

        def _ask():
            result["code"], result["out"], result["err"] = run(
                "ask", "--to", other, "--timeout", "3", "--json", "what is the answer?"
            )

        t = threading.Thread(target=_ask)
        t.start()

        req_dir = home / "rpc" / "requests"
        req_path = None
        for _ in range(100):
            paths = list(req_dir.glob("*.json"))
            if paths:
                req_path = paths[0]
                break
            time.sleep(0.02)
        assert req_path is not None, "RPC request was not created"

        req = json.loads(req_path.read_text(encoding="utf-8"))
        assert req["from"] == "tester"
        assert req["to"] == other
        assert req["question"] == "what is the answer?"
        assert req["timeout"] == 3

        resp_path = home / "rpc" / "responses" / f"{req['id']}.json"
        resp_path.parent.mkdir(parents=True, exist_ok=True)
        resp_path.write_text(
            json.dumps({"call_id": req["id"], "from": other, "answer": "forty-two"}),
            encoding="utf-8",
        )

        t.join(timeout=5)
        assert result["code"] == 0
        assert result["out"]["data"]["call_id"] == req["id"]
        assert result["out"]["data"]["from"] == other
        assert result["out"]["data"]["answer"] == "forty-two"

    def test_ask_timeout(self, run, home, other):
        code, out, _ = run("ask", "--to", other, "--timeout", "1", "--json", "slow question")
        assert code == 124
        assert out["reason"] == "ask_timeout"
        assert out["data"]["to"] == other
        requests = list((home / "rpc" / "requests").glob("*.json"))
        assert len(requests) == 1

    def test_ask_self_forbidden(self, run, profile):
        code, out, _ = run("ask", "--to", profile, "--timeout", "1", "--json", "self")
        assert code == 4
        assert out["reason"] == "self_ask_forbidden"

    def test_ask_invalid_profile(self, run):
        code, out, _ = run("ask", "--to", "bad profile!", "--timeout", "1", "--json", "q")
        assert code == 4
        assert out["reason"] == "invalid_profile_name"
