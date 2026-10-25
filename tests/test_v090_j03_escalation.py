"""v0.9.0 §1 (J-03): deadline escalation."""
import json
from datetime import datetime, timezone, timedelta


def _make_task(run, title="t", owner=None, deadline=None, **kw):
    args = ["task", "new", title]
    if owner:
        args += ["--owner", owner]
    if deadline:
        args += ["--deadline", deadline]
    for k, v in kw.items():
        if v is None:
            continue
        args += [f"--{k.replace('_', '-')}", str(v)]
    code, out, err = run(*args)
    assert code == 0, (out, err)
    return out["data"]["id"]


def test_escalate_past_due_creates_mail_and_dedups(run, home, profile, other):
    past = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
    tid = _make_task(run, "overdue", owner=other, deadline=past)
    code, out, err = run("task", "escalate", "--due", tid, profile_override=other)
    assert code == 0, (out, err)
    assert out["data"]["escalated"] is True
    assert out["data"]["severity"] == "critical"
    assert len(list((home / "mail" / other / "inbox").glob("*.md"))) == 1
    # double call dedups
    code2, out2, _ = run("task", "escalate", "--due", tid, profile_override=other)
    assert code2 == 0
    assert out2["data"].get("deduplicated") is True
    assert len(list((home / "mail" / other / "inbox").glob("*.md"))) == 1


def test_escalate_future_due_no_mail(run, home, profile, other):
    future = (datetime.now(timezone.utc).date() + timedelta(days=3)).isoformat()
    tid = _make_task(run, "future", owner=other, deadline=future)
    code, out, err = run("task", "escalate", "--due", tid, profile_override=other)
    assert code == 0, (out, err)
    assert out["data"]["escalated"] is False
    inbox = home / "mail" / other / "inbox"
    assert not inbox.exists() or not list(inbox.glob("*.md"))


def test_overdue_filter(run, home, profile, other):
    today = datetime.now(timezone.utc).date()
    past = (today - timedelta(days=2)).isoformat()
    future = (today + timedelta(days=3)).isoformat()
    late = _make_task(run, "late", owner=other, deadline=past)
    okd = _make_task(run, "ok", owner=other, deadline=future)
    code, out, err = run("task", "list", "--overdue", profile_override=other)
    assert code == 0, err
    ids = [e["id"] for e in out["data"]["entries"]]
    assert late in ids
    assert okd not in ids


def test_doctor_overdue_counter(run, home, profile, other):
    past = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
    _make_task(run, "late", owner=other, deadline=past)
    code, out, err = run("doctor", "run", profile_override=other)
    assert "overdue" in json.dumps(out)
    assert out["data"].get("overdue_tasks", out["data"].get("overdue", 0)) >= 1
