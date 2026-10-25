"""v0.6.0 §3 (H-01): operate-аудит — task_* переходы, bundle recovery в аудит.
Формат записи существующий (ts/event/caller/target/details). Шум не растёт:
read-only команды не пишут событий."""
from __future__ import annotations

import hashlib
import json


def _audit_lines(home):
    p = home / ".state" / "audit.log"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _events(home):
    return [e["event"] for e in _audit_lines(home)]


def _make_task(run, title="job", owner=None):
    args = ["task", "new", title]
    if owner:
        args += ["--owner", owner]
    code, out, _ = run(*args)
    assert code == 0, out
    return out["data"]["id"]


def test_task_lifecycle_writes_operate_events(run, home, other):
    tid = _make_task(run, "жизненный цикл", owner=other)
    run("task", "claim", tid, profile_override=other)
    run("task", "submit", tid, profile_override=other)
    run("task", "accept", tid, "--no-evidence", profile_override=other)
    lines = _audit_lines(home)
    events = [e["event"] for e in lines]
    assert "task_new" in events and "task_claim" in events
    assert "task_submit" in events and "task_accept" in events
    ev = next(e for e in lines if e["event"] == "task_claim")
    assert ev["caller"] == other
    assert ev["target"] == tid
    assert ev["details"]["from"] == "assigned" and ev["details"]["to"] == "in_progress"
    assert "ts" in ev and ev["ts"].endswith("Z")


def test_task_cancel_cascade_event(run, home):
    pid = _make_task(run, "родитель")
    cid = _make_task(run, "дитя")
    run("task", "link", cid, "--journal", "dummy-nonexistent", check=False)  # не влияет
    # child via parent flag
    code, out, _ = run("task", "new", "ребёнок", "--parent", pid)
    kid = out["data"]["id"]
    run("task", "cancel", pid, "--reason", "нет смысла")
    events = _events(home)
    assert "task_cancel" in events and "task_cancel_cascade" in events
    lines = _audit_lines(home)
    casc = next(e for e in lines if e["event"] == "task_cancel_cascade")
    assert kid in json.dumps(casc["details"])


def test_read_only_commands_add_no_audit(run, home, other):
    _make_task(run, "для чтения")
    run("mail", "send", "--to", other, "--subject", "hi", "--body", "text")
    before = len(_audit_lines(home))
    run("task", "list")
    run("task", "board")
    run("journal", "--all")
    run("mail", "list", "--inbox")
    run("mail", "check")
    after = len(_audit_lines(home))
    assert after == before  # только state-changing + privileged-read


def test_doctor_bundle_recovery_logged(run, home, tmp_path):
    # hand-crafted pending txn: target не на месте, tmp лежит рядом
    target = home / "journal" / "2026" / "10" / "recovered_record.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = "---\ntype: journal\nid: rec1\nauthor: tester\naccess: L2\ncreated_at: 2026-10-23T00:00:00Z\nwhat_done: восстановлено\n---\nbody\n"
    tmp = target.with_name(target.name + ".bundle-x.tmp")
    tmp.write_text(content, encoding="utf-8")
    txn_dir = home / ".state" / "bundle_txn"
    txn_dir.mkdir(parents=True, exist_ok=True)
    doc = {"id": "txn1", "label": "mail:test", "created_at": "2026-10-23T00:00:00Z",
           "state": "pending",
           "ops": [{"target": str(target), "tmp": str(tmp),
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}]}
    (txn_dir / "txn1.bundle_txn").write_text(json.dumps(doc), encoding="utf-8")
    code, out, _ = run("doctor", check=False)  # issues → degraded rc
    assert out["data"]["bundle_recovery"]["applied_ops"] == 1
    assert target.exists()
    assert "bundle_recovery" in _events(home)
