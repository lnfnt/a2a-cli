"""v0.5.0: commit_bundle + crash recovery (DESIGN-A2A-V05 §2).

Fault injection via monkeypatched os.replace — these tests run the a2a
modules IN-PROCESS with util.HOME pointed at a tmp bus (never the real
shared_workspace). Recovery is checked both directly (util.recover_bundles)
and through `a2a doctor` (subprocess, same isolated bus).
"""
import errno
import glob as globmod
import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from a2a import mail, util

REPO = str(Path(__file__).resolve().parent.parent)


@pytest.fixture
def bus(tmp_path, monkeypatch):
    home = tmp_path / "bus"
    home.mkdir()
    monkeypatch.setattr(util, "HOME", home)
    monkeypatch.setenv("A2A_HOME", str(home))
    monkeypatch.setenv("A2A_PROFILE", "tester")
    monkeypatch.setenv("A2A_USER_FEED", str(tmp_path / "notesapp"))
    return home


def _make_flaky_replace(monkeypatch, dst_filter):
    """Raise IOError on every os.replace whose destination matches dst_filter.

    Returns a restore() callable — NOT monkeypatch.undo(), which would also
    drop the bus fixture's HOME/env patches.
    """
    real = os.replace

    def flaky(src, dst, *a, **k):
        if dst_filter(str(dst)):
            raise OSError(errno.EIO, "injected fs failure", str(dst))
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", flaky)
    return lambda: monkeypatch.setattr(os, "replace", real)


def test_commit_bundle_clean(bus, tmp_path):
    ops = [(tmp_path / "a" / "one.md", "one"), (tmp_path / "b" / "two.md", "two")]
    landed = util.commit_bundle(ops, label="test")
    assert sorted(p.name for p in landed) == ["one.md", "two.md"]
    assert (tmp_path / "a" / "one.md").read_text() == "one"
    # no journal residue, no stray bundle tmps
    assert list(util.bundle_txn_dir().glob("*.bundle_txn")) == []
    assert globmod.glob(str(tmp_path / "**" / "*.bundle-*.tmp"), recursive=True) == []


def test_mail_send_crash_midway_then_recovery(bus, monkeypatch):
    killer = lambda dst: "/mail/user-profile/inbox/" in dst
    restore = _make_flaky_replace(monkeypatch, killer)
    args = Namespace(to="user-profile", subject="s", body="b", priority="fyi", ref=None,
                     access=None, wake=None, depth=1, timeout=60,
                     profile=None, as_=None, idempotency_key=None)
    with pytest.raises(SystemExit) as exc:
        mail.cmd_mail_send(args)
    assert exc.value.code == util.EXIT_ERROR
    restore()  # back to real os.replace, bus fixture stays intact

    outbox = list((bus / "mail" / "tester" / "outbox").glob("*.md"))
    inbox = list((bus / "mail" / "user-profile" / "inbox").glob("*.md"))
    txns = list(util.bundle_txn_dir().glob("*.bundle_txn"))
    assert len(outbox) == 1 and inbox == [] and len(txns) == 1, \
        "crash must leave a healed-by-recovery half-state, not random corruption"

    rec = util.recover_bundles()
    assert rec["applied_ops"] == 1
    inbox_after = list((bus / "mail" / "user-profile" / "inbox").glob("*.md"))
    assert len(inbox_after) == 1
    assert inbox_after[0].read_text(encoding="utf-8") == outbox[0].read_text(encoding="utf-8")
    # no duplicates, no leftovers
    assert len(list((bus / "mail" / "user-profile" / "inbox").rglob("*.md"))) == 1
    assert list(util.bundle_txn_dir().glob("*.bundle_txn")) == []

    # idempotent second run
    rec2 = util.recover_bundles()
    assert rec2["applied_ops"] == 0 and rec2["scanned"] == 0


def test_task_transition_crash_then_recovery(bus, monkeypatch):
    from a2a import tasks

    # create task in-process (ends with util.ok -> SystemExit 0)
    args = Namespace(title="crashy", owner="tester", goal=None, acceptance=None,
                     deadline=None, parent=None, priority="today", access=None,
                     acl=None, idempotency_key=None, profile=None, as_=None)
    with pytest.raises(SystemExit) as exc:
        tasks.cmd_task_new(args, "tester")
    assert exc.value.code == 0
    task_files = list((bus / "tasks").glob("*.md"))
    assert len(task_files) == 1
    tid = util.read_record(task_files[0])["id"]

    # claim bundles task.md + journal.md; kill the journal rename
    restore = _make_flaky_replace(monkeypatch, lambda dst: "/journal/" in dst)
    claim_args = Namespace(id=tid, reason=None, include_l1=None, evidence=None,
                           no_evidence=None, profile=None, as_=None)
    with pytest.raises(SystemExit) as exc2:
        tasks._apply_transition(claim_args, "tester", "claim")
    assert exc2.value.code == util.EXIT_ERROR
    restore()

    # task.md landed (rename wave order), journal missing -> txn pending
    assert list(util.bundle_txn_dir().glob("*.bundle_txn"))
    rec = util.recover_bundles()
    assert rec["applied_ops"] == 1
    journal_files = list((bus / "journal").rglob("*.md"))
    assert len(journal_files) == 2  # creation note + claim note
    healed = util.read_record(task_files[0])
    assert healed["status"] == "in_progress"
    assert len(healed["links"]["journal"]) == 2
    # both journal ids now physically exist
    jids = healed["links"]["journal"]
    on_disk = {util.read_record(p)["id"] for p in journal_files}
    assert set(jids) <= on_disk
    # recovery idempotent again
    assert util.recover_bundles()["scanned"] == 0


def test_doctor_recovers_via_cli_and_second_run_is_clean(bus):
    # craft a half-committed bundle exactly like a crash would:
    from a2a import util as u
    target = bus / "mail" / "user-profile" / "inbox" / "2026-10-23_deadbeef.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_suffix(".md.bundle-staged.tmp")
    content = "---\ntype: mail\nid: deadbeef\nauthor: tester\nto: user-profile\naccess: L2\nsubject: crashy\ncreated_at: 2026-10-23T00:00:00Z\n---\nbody\n"
    staged.write_text(content, encoding="utf-8")
    import hashlib, json as _json
    txn = {
        "id": "testtxn", "label": "manual", "created_at": u.utc_iso(), "state": "pending",
        "ops": [{"target": str(target), "tmp": str(staged),
                 "sha256": hashlib.sha256(content.encode()).hexdigest()}],
    }
    txn_dir = u.bundle_txn_dir()
    txn_dir.mkdir(parents=True, exist_ok=True)
    (txn_dir / "testtxn.bundle_txn").write_text(_json.dumps(txn), encoding="utf-8")

    env = dict(os.environ, A2A_HOME=str(bus), A2A_PROFILE="tester", PATH="/usr/bin:/bin")
    p = subprocess.run([sys.executable, "-m", "a2a.cli", "doctor"],
                       capture_output=True, text=True, env=env, cwd=REPO)
    assert p.returncode == util.EXIT_DEGRADED  # healed, but flagged honestly
    out = json.loads(p.stdout)
    assert any(i.get("type") == "bundle_txn_recovered" for i in out["data"]["issues"])
    assert target.exists() and not staged.exists()
    assert not list(txn_dir.glob("*.bundle_txn"))

    # second doctor: nothing to recover (idempotent recovery, clean run)
    p2 = subprocess.run([sys.executable, "-m", "a2a.cli", "doctor"],
                        capture_output=True, text=True, env=env, cwd=REPO)
    out2 = json.loads(p2.stdout) if p2.stdout.strip() else {}
    assert not any(i.get("type") == "bundle_txn_recovered" for i in out2.get("data", {}).get("issues", []))


def test_broken_bundle_journal_is_reported_not_hallucinated(bus):
    txn_dir = util.bundle_txn_dir()
    txn_dir.mkdir(parents=True, exist_ok=True)
    (txn_dir / "broken.bundle_txn").write_text("{not json", encoding="utf-8")
    rec = util.recover_bundles()
    assert rec["bundles_pending"] == 1
    assert rec["unrecoverable"]
    # corrupt txn is preserved for diagnosis, never silently deleted
    assert (txn_dir / "broken.bundle_txn").exists()
