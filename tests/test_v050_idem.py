"""v0.5.0: --idempotency-key for task new / mail send (DESIGN-A2A-V05 §3).

Duplicate key inside 24h window returns the original id with
duplicate_suppressed metadata; storage is .state/idempotency.sqlite (flock).
"""
import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta


def test_task_new_idempotent_same_id_once(run, home):
    code, out, err = run("task", "new", "идемпотентный", "--idempotency-key", "K1")
    assert code == 0, err
    first = out["data"]["id"]
    code2, out2, err2 = run("task", "new", "идемпотентный", "--idempotency-key", "K1")
    assert code2 == 0, err2
    assert out2["data"]["id"] == first
    assert out2["data"].get("duplicate_suppressed") is True
    assert out2["metadata"].get("duplicate_suppressed") is True
    # exactly one record on the bus
    assert len(list((home / "tasks").glob("*.md"))) == 1


def test_task_new_different_keys_distinct(run, home):
    _, o1, _ = run("task", "new", "разный 1", "--idempotency-key", "KA")
    _, o2, _ = run("task", "new", "разный 2", "--idempotency-key", "KB")
    assert o1["data"]["id"] != o2["data"]["id"]
    assert len(list((home / "tasks").glob("*.md"))) == 1 + 1


def test_mail_send_idempotent_no_double_delivery(run, home, other):
    code, out, err = run("mail", "send", "--to", other, "--subject", "дубль",
                         "--body", "x", "--idempotency-key", "MK")
    assert code == 0, err
    mid = out["data"]["id"]
    code2, out2, _ = run("mail", "send", "--to", other, "--subject", "дубль",
                         "--body", "x", "--idempotency-key", "MK")
    assert code2 == 0
    assert out2["data"]["id"] == mid
    assert out2["metadata"].get("duplicate_suppressed") is True
    assert len(list((home / "mail" / other / "inbox").glob("*.md"))) == 1
    assert len(list((home / "mail" / "tester" / "outbox").glob("*.md"))) == 1


def test_window_expiry_24h_allows_retry(run, home, tmp_path):
    _, o1, _ = run("task", "new", "окно", "--idempotency-key", "W1")
    first = o1["data"]["id"]
    # age the row beyond the 24h window
    db = home / ".state" / "idempotency.sqlite"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE idempotency SET created_at = ?", (time.time() - 25 * 3600,))
    conn.commit()
    conn.close()
    _, o2, _ = run("task", "new", "окно", "--idempotency-key", "W1")
    assert o2["data"]["id"] != first  # expired -> new record allowed
    # and the fresh key suppresses again
    _, o3, _ = run("task", "new", "окно", "--idempotency-key", "W1")
    assert o3["data"]["id"] == o2["data"]["id"]
    assert o3["metadata"].get("duplicate_suppressed") is True


def test_scope_isolation_task_vs_mail_key(run, home, other):
    # same key string, different command scopes -> no false suppression
    _, t, _ = run("task", "new", "скоуп", "--idempotency-key", "SHARED")
    _, m, _ = run("mail", "send", "--to", other, "--subject", "скоуп", "--body", "b",
                  "--idempotency-key", "SHARED")
    assert t["data"]["id"] != m["data"]["id"]
    assert not t["data"].get("duplicate_suppressed")
    assert not m["data"].get("duplicate_suppressed")
