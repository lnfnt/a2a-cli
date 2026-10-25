"""v0.8.0 §2: digest — сводка для вызывающего (координационные тесты)."""
from __future__ import annotations


def test_digest_empty_bus_ok(run, home, profile):
    code, out, _ = run("digest")
    assert code == 0, out
    d = out["data"]
    for key in ("unread_inbox", "unanswered_outbox", "tasks", "alerts"):
        assert d.get(key) == [] or isinstance(d.get(key), list), (key, d)


def test_digest_shows_unread_inbox(run, home, profile, other):
    code, out, _ = run("mail", "send", "--to", other, "--subject", "s1", "--body", "b1",
                       profile_override=profile)
    assert code == 0
    code, d1, _ = run("digest", profile_override=other)
    assert code == 0
    assert d1["data"]["unread_inbox"], "letter must appear in recipient digest"
    code, d2, _ = run("digest", profile_override=profile)
    assert not d2["data"]["unread_inbox"], "sender digest must not list own outbox as unread"


def test_digest_mail_flag_sends_exactly_one(run, home, profile, other):
    code, out, _ = run("mail", "send", "--to", other, "--subject", "x", "--body", "y",
                       profile_override=profile)
    code, d, _ = run("digest", "--mail", profile_override=other)
    assert code == 0, d
    inbox = list((home / "mail" / other / "inbox").glob("*.md"))
    # original + digest-self = exactly one more
    assert len(inbox) == 2, inbox
    digest_letters = [i for i in inbox if "digest" in i.read_text(encoding="utf-8").lower()
                      or "сводк" in i.read_text(encoding="utf-8").lower()]
    assert digest_letters, "digest letter expected"


def test_digest_tasks_alerts_sections(run, home, profile):
    code, out, _ = run("task", "new", "digest-task", "--owner", profile, "--goal", "g")
    assert code == 0
    code, d, _ = run("digest")
    assert code == 0
    assert any("digest-task" in str(t) for t in d["data"]["tasks"]), d["data"]
