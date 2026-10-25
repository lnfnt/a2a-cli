"""v0.6.0 §2 (F-01): write-scope — переходы/правки чужой задачи только участниками;
админ-обход --as-owner с аудит-событием write_scope_override.
Journal/mail: автор всегда вызывающий (отдельного author-флага нет) — проверяем."""
from __future__ import annotations

import json


def _make_task(run, title="job", owner=None):
    args = ["task", "new", title]
    if owner:
        args += ["--owner", owner]
    code, out, _ = run(*args)
    assert code == 0, out
    return out["data"]["id"]


def _audit(home):
    p = home / ".state" / "audit.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _to_review(run, tid, other):
    run("task", "claim", tid, profile_override=other)
    run("task", "submit", tid, profile_override=other)


def test_claim_by_non_participant_rejected_invalid_actor(run, home, profile, other):
    tid = _make_task(run, "чужая работа")  # author=tester, owner=tester
    code, out, _ = run("task", "claim", tid, profile_override="randprof-z", check=False)
    assert code != 0
    assert "invalid_actor" in (out.get("reason") or "")
    hint = json.dumps(out.get("data", {}))
    assert "author" in hint and "owner" in hint and "assignee" in hint


def test_legacy_not_participant_reason_substring_kept(run, home, profile, other):
    tid = _make_task(run, "приёмка чужими", owner=other)
    _to_review(run, tid, other)
    code, out, _ = run("task", "accept", tid, "--no-evidence",
                       profile_override="randprof-z", check=False)
    assert code != 0
    reason = out.get("reason") or ""
    # оба контракта: новый invalid_actor и старый not_participant (подстрока)
    assert "invalid_actor" in reason and "not_participant" in reason


def test_claim_by_owner_still_allowed(run, home, profile, other):
    tid = _make_task(run, "назначено other", owner=other)
    code, out, _ = run("task", "claim", tid, profile_override=other)
    assert code == 0, out
    assert out["data"]["status"] == "in_progress"


def test_as_owner_override_transitions_with_audit(run, home, profile, other):
    tid = _make_task(run, "обход администрации")
    code, out, _ = run("task", "claim", tid, "--as-owner",
                       profile_override="randprof-z", check=False)
    assert code == 0, out
    assert out["data"]["status"] == "in_progress"
    assert "write_scope_override" in _audit(home)
    assert "randprof-z" in _audit(home)


def test_as_owner_override_accept_with_audit(run, home, profile, other):
    tid = _make_task(run, "обход приёмки", owner=other)
    _to_review(run, tid, other)
    code, out, _ = run("task", "accept", tid, "--no-evidence", "--as-owner",
                       profile_override="randprof-z", check=False)
    assert code == 0, out
    assert out["data"]["status"] == "done"
    audit = _audit(home)
    assert audit.count("write_scope_override") == 1


def test_task_link_by_non_participant_rejected(run, home, profile, other):
    tid = _make_task(run, "чужие link")
    _, lg, _ = run("log", "запись для линка", profile_override="randprof-z")
    code, out, _ = run("task", "link", tid, "--journal", lg["data"]["id"],
                       profile_override="randprof-z", check=False)
    assert code != 0
    assert "invalid_actor" in (out.get("reason") or "")


def test_journal_author_always_caller_no_author_flag(run, home, other):
    # отдельного --author нет: автор записи = идентификатор вызывающего
    code, out, _ = run("log", "запись с числом 4000 багов", "--author", other, check=False)
    assert code != 0  # неизвестный флаг отвергается парсером
    code, lg, _ = run("log", "обычная запись", profile_override=other)
    assert code == 0
    files = list((home / "journal").rglob(f"*_{lg['data']['id']}.md"))
    text = files[0].read_text(encoding="utf-8")
    assert f"author: {other}" in text
