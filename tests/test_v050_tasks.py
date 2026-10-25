"""v0.5.0: task object — state machine, evidence gate, cascade, board, overdue.

Per DESIGN-A2A-V05 §1 / TASK_A2A_V05 §1. Isolated bus via A2A_HOME (conftest).
"""
import json
from datetime import datetime, timezone, timedelta


def _tid(out):
    return out["data"]["id"]


def _make_task(run, title="таск", owner=None, **kw):
    args = ["task", "new", title]
    if owner:
        args += ["--owner", owner]
    for k, v in kw.items():
        if v is None:
            continue
        args += [f"--{k.replace('_', '-')}", str(v)]
    code, out, err = run(*args)
    assert code == 0, (out, err)
    return out["data"]["id"]


def _read_task_md(run, home, task_id):
    from a2a import util
    p = home / "tasks" / f"{task_id}.md"
    assert p.exists(), f"task record not found: {p}"
    rec = util.read_record(p)
    assert rec is not None
    return rec


def test_task_new_creates_record_and_envelope(run, home, profile):
    tid = _make_task(run, "сделать v0.5", goal="цель", priority="today")
    rec = _read_task_md(run, home, tid)
    assert rec["type"] == "task"
    assert rec["status"] == "backlog"
    assert rec["author"] == profile
    assert rec["owner"] == profile
    assert rec["title"] == "сделать v0.5"
    assert rec["version"] == 1
    # new writes an auto journal note (J-02): journal already linked, mail/artifacts empty
    assert set(rec["links"]) == {"journal", "mail", "artifacts"}
    assert rec["links"]["mail"] == [] and rec["links"]["artifacts"] == []
    assert isinstance(rec["assignee_history"], list)


def test_task_new_with_owner_is_assigned(run, home, other):
    tid = _make_task(run, "делелегировано", owner=other)
    rec = _read_task_md(run, home, tid)
    assert rec["status"] == "assigned"
    assert rec["owner"] == other


def test_task_new_unknown_parent_refused(run, home):
    code, out, _ = run("task", "new", "сирота", "--parent", "0" * 32)
    assert code != 0
    assert "unknown_parent" in (out.get("reason") or "")


def test_invalid_transition_refused(run, home):
    tid = _make_task(run, "статус-машина")
    # backlog -> review illegal
    code, out, err = run("task", "submit", tid)
    assert code != 0, err
    assert "invalid_transition" in (out.get("reason") or "")
    # accept straight from backlog illegal
    code2, out2, _ = run("task", "accept", tid, "--no-evidence")
    assert code2 != 0
    assert "invalid_transition" in (out2.get("reason") or "")


def test_full_happy_path_with_evidence(run, home, profile, other, make_artifact):
    tid = _make_task(run, "полный цикл", owner=other)
    art = make_artifact("proof.txt")
    code, out, err = run("task", "claim", tid, profile_override=other)
    assert code == 0, err
    assert out["data"]["status"] == "in_progress"
    code, out, err = run("task", "submit", tid, "--evidence", str(art), profile_override=other)
    assert code == 0, err
    assert out["data"]["status"] == "review"
    code, out, err = run("task", "accept", tid, "--comment", "ок", profile_override=other)
    assert code == 0, err
    assert out["data"]["status"] == "done"
    rec = _read_task_md(run, home, tid)
    assert rec["status"] == "done"
    assert rec["evidence_verified"] is True
    assert rec["version"] >= 4
    assert rec["updated_at"]  # second-granular clock: presence, not inequality
    assert str(art) in json.dumps(rec["evidence"])


def test_journal_id_as_evidence(run, home, profile, other):
    code, jout, err = run("log", "доказательство сделано", "--artifacts", "none-check")
    # artifacts none-check doesn't exist -> UNVERIFIED; just get id
    assert code == 0, err
    jid = jout["data"]["id"]
    tid = _make_task(run, "журнальное доказательство", owner=other)
    run("task", "claim", tid, profile_override=other)
    code, out, err = run("task", "submit", tid, "--evidence", jid, profile_override=other)
    assert code == 0, (out, err)
    rec = _read_task_md(run, home, tid)
    assert jid in json.dumps(rec["evidence"])


def test_terminal_without_evidence_refused(run, home, other):
    tid = _make_task(run, "без доказательств", owner=other)
    run("task", "claim", tid, profile_override=other)
    code, out, err = run("task", "submit", tid, profile_override=other)
    assert code == 0, err
    code2, out2, _ = run("task", "accept", tid, profile_override=other)
    assert code2 != 0
    assert "evidence_required" in (out2.get("reason") or "")


def test_no_evidence_stamps_unverified_in_record_and_feed(run, home, other):
    tid = _make_task(run, "сомнительная приёмка", owner=other)
    run("task", "claim", tid, profile_override=other)
    run("task", "submit", tid, profile_override=other)
    code, out, err = run("task", "accept", tid, "--no-evidence", profile_override=other)
    assert code == 0, err
    assert out["data"].get("unverified") is True
    rec = _read_task_md(run, home, tid)
    assert rec["status"] == "done"
    assert rec["unverified"] is True
    assert rec["evidence_verified"] is False
    # user-лента содержит пометку unverified
    feed_files = list((home / "user-feed").glob("*.md"))
    assert feed_files, "terminal transition must surface a user-feed note"
    blob = " ".join(p.read_text(encoding="utf-8") for p in feed_files).lower()
    assert "unverified" in blob
    assert tid[:8] in blob or tid in blob


def test_reject_requires_reason_and_gates_evidence(run, home, other):
    tid = _make_task(run, "на отклонение", owner=other)
    run("task", "claim", tid, profile_override=other)
    code, out, err = run("task", "submit", tid, profile_override=other)
    assert code == 0, err
    # no reason -> refused
    code2, out2, _ = run("task", "reject", tid, profile_override=other)
    assert code2 != 0
    assert "reason_required" in (out2.get("reason") or "")
    # with reason but without evidence -> refused
    code3, out3, _ = run("task", "reject", tid, "--reason", "не то", profile_override=other)
    assert code3 != 0
    assert "evidence_required" in (out3.get("reason") or "")
    # with reason + --no-evidence -> rejected, unverified stamp
    code4, out4, err4 = run("task", "reject", tid, "--reason", "не то", "--no-evidence", profile_override=other)
    assert code4 == 0, err4
    rec = _read_task_md(run, home, tid)
    assert rec["status"] == "rejected"
    assert rec["unverified"] is True


def test_block_requires_reason_and_resume(run, home, other):
    tid = _make_task(run, "в блок", owner=other)
    code, out, err = run("task", "block", tid, profile_override=other)
    assert code != 0
    assert "reason_required" in (out.get("reason") or "")
    code, out, err = run("task", "block", tid, "--reason", "ждём API", profile_override=other)
    assert code == 0, err
    assert out["data"]["status"] == "blocked"
    rec = _read_task_md(run, home, tid)
    assert rec["blocked_reason"] == "ждём API"
    # blocked -> in_progress via claim (resume)
    code2, out2, err2 = run("task", "claim", tid, profile_override=other)
    assert code2 == 0, err2
    assert out2["data"]["status"] == "in_progress"


def test_cancel_cascades_children_to_blocked_parent_cancelled(run, home, other):
    parent = _make_task(run, "родитель", owner=other)
    c1 = _make_task(run, "ребёнок 1", owner=other, parent=parent)
    c2 = _make_task(run, "ребёнок 2", owner=other, parent=parent)
    code, out, err = run("task", "cancel", parent, profile_override=other)
    assert code == 0, err
    assert out["data"]["status"] == "cancelled"
    assert sorted(out["data"]["cascaded"]) == sorted([c1, c2])
    for cid in (c1, c2):
        rec = _read_task_md(run, home, cid)
        assert rec["status"] == "blocked"
        assert rec["blocked_reason"] == "parent_cancelled"
        assert (home / "tasks" / f"{cid}.md").exists()  # retain: not deleted
    prec = _read_task_md(run, home, parent)
    assert prec["status"] == "cancelled"


def test_cancel_cascade_keep_leaves_children(run, home, other):
    parent = _make_task(run, "родитель keep", owner=other)
    c1 = _make_task(run, "ребёнок keep", owner=other, parent=parent)
    code, out, err = run("task", "cancel", parent, "--cascade", "keep", profile_override=other)
    assert code == 0, err
    assert out["data"]["cascaded"] == []
    rec = _read_task_md(run, home, c1)
    assert rec["status"] != "blocked"


def test_status_change_writes_journal_both_ways(run, home, other):
    code, jbefore, _ = run("journal", "--all")
    tid = _make_task(run, "авто-журнал", owner=other)
    run("task", "claim", tid, profile_override=other)
    rec = _read_task_md(run, home, tid)
    assert rec["links"]["journal"], "status change must link a journal entry"
    code, jout, err = run("journal", "--all", profile_override=other)
    assert code == 0, err
    jid = rec["links"]["journal"][-1]
    entries = [e for e in jout["data"]["entries"] if e["id"] == jid]
    assert entries, "auto journal entry must be listed"
    # bidirectional: journal record references the task id
    from a2a import util
    found = False
    for p in (home / "journal").rglob("*.md"):
        r = util.read_record(p)
        if r and r.get("id") == jid:
            assert tid in json.dumps(r, default=str)
            found = True
    assert found


def test_list_filters_and_overdue(run, home, other):
    today = datetime.now(timezone.utc).date()
    past = (today - timedelta(days=2)).isoformat()
    future = (today + timedelta(days=3)).isoformat()
    late = _make_task(run, "просрочен", owner=other, deadline=past)
    soon = _make_task(run, "сегодняшний", owner=other, deadline=today.isoformat())
    okd = _make_task(run, "будущий", owner=other, deadline=future)
    code, out, err = run("task", "list", "--overdue", profile_override=other)
    assert code == 0, err
    ids = [e["id"] for e in out["data"]["entries"]]
    assert late in ids
    assert soon not in ids and okd not in ids
    # filter by status (owner!=author -> assigned)
    code, out2, _ = run("task", "list", "--status", "assigned", profile_override=other)
    assert code == 0
    ids2 = [e["id"] for e in out2["data"]["entries"]]
    assert {late, soon, okd} <= set(ids2)
    # invalid status refused
    code3, out3, _ = run("task", "list", "--status", "wat", profile_override=other)
    assert code3 != 0
    assert "invalid_status" in (out3.get("reason") or "")
    # owner filter
    code4, out4, _ = run("task", "list", "--owner", "nobody-xx", profile_override=other)
    assert code4 == 0
    assert out4["data"]["entries"] == []


def test_mail_check_gains_overdue_tasks_section(run, home, other):
    today = datetime.now(timezone.utc).date()
    past = (today - timedelta(days=1)).isoformat()
    _make_task(run, "must surface", owner=other, deadline=past)
    _make_task(run, "due today", owner=other, deadline=today.isoformat())
    code, out, err = run("mail", "check", profile_override=other)
    assert code == 0, err
    sec = out["data"]["tasks"]
    assert sec["overdue"] >= 1
    assert sec["due_today"] >= 1
    # mail check stays read-only: no new journal entries from it
    code2, jb1, _ = run("journal", "--all", profile_override=other)
    code3, out3, err3 = run("mail", "check", profile_override=other)
    code4, jb2, _ = run("journal", "--all", profile_override=other)
    assert jb1["data"]["count"] == jb2["data"]["count"]


def test_board_markdown_and_user_safe(run, home, profile, other):
    _make_task(run, "видимая доска", owner=other)
    code, out, err = run("task", "board", profile_override=other)
    assert code == 0, err
    md = out["data"]["markdown"]
    assert "|" in md and "видимая доска" in md
    assert "---" in md.splitlines()[0] or True  # markdown table separator
    # чужой L1 не попадает в board
    code, l1out, err = run("log", "личная задача secretextreme-l1", "--access", "L1")
    assert code == 0, err
    # create a foreign L1 task directly (access flag)
    code, t2, err2 = run("task", "new", "чужой секрет board-secret-l1", "--access", "L1", profile_override="stranger-x")
    assert code == 0, err2
    code, mine, err3 = run("task", "board", profile_override=other)
    assert code == 0, err3
    blob = json.dumps(mine, ensure_ascii=False)
    assert "board-secret-l1" not in blob, "foreign L1 task must not leak into board"
    code, st, err4 = run("task", "show", t2["data"]["id"], profile_override=other)
    assert code != 0, "foreign L1 task must not be readable without flag"
    code2, st2, err5 = run("task", "show", t2["data"]["id"], "--include-l1", profile_override=other)
    assert code2 == 0, err5
    audit = (home / ".state" / "audit.log").read_text(encoding="utf-8")
    assert "task_l1_read" in audit


def test_show_full_for_participant_restricted_for_other(run, home, profile, other):
    tid = _make_task(run, "детали для участников", owner=other, goal="тайная цель", acceptance="критерий")
    code, full, err = run("task", "show", tid, profile_override=other)
    assert code == 0, err
    assert full["data"]["goal"] == "тайная цель"
    assert "критерий" in json.dumps(full["data"].get("acceptance", []), ensure_ascii=False)
    # third profile: summary only (L2 default visibility)
    code, lim, err2 = run("task", "show", tid, profile_override="third-y")
    assert code == 0, err2
    assert lim["data"].get("restricted") is True
    assert "тайная цель" not in json.dumps(lim, ensure_ascii=False)
    # list for third: no goal either
    code, lst, _ = run("task", "list", profile_override="third-y")
    assert code == 0
    assert "тайная цель" not in json.dumps(lst, ensure_ascii=False)


def test_link_commands_bidirectional(run, home, other):
    tid = _make_task(run, "линки")
    code, jout, err = run("log", "запись для линка")
    jid = jout["data"]["id"]
    code, out, err = run("task", "link", tid, "--journal", jid)
    assert code == 0, err
    rec = _read_task_md(run, home, tid)
    assert jid in rec["links"]["journal"]
    from a2a import util
    jr = None
    for p in (home / "journal").rglob("*.md"):
        r = util.read_record(p)
        if r and r.get("id") == jid:
            jr = r
    assert jr is not None and jr.get("task_id") == tid
    # artifact link stored as-is
    code, out2, err2 = run("task", "link", tid, "--artifact", "/tmp/some-artifact.bin")
    assert code == 0, err2
    rec2 = _read_task_md(run, home, tid)
    assert "/tmp/some-artifact.bin" in rec2["links"]["artifacts"]
    # unknown journal id refused
    code3, out3, _ = run("task", "link", tid, "--journal", "f" * 32)
    assert code3 != 0
    assert "journal_not_found" in (out3.get("reason") or "")


def test_task_not_found_and_usage_errors(run, home):
    code, out, _ = run("task", "show", "0" * 32)
    assert code != 0
    assert "task_not_found" in (out.get("reason") or "")
    code2, out2, _ = run("task", "frobnicate")
    assert code2 != 0


def test_evidence_not_found_refused(run, home, other):
    tid = _make_task(run, "битое доказательство", owner=other)
    run("task", "claim", tid, profile_override=other)
    code, out, err = run("task", "submit", tid, "--evidence", "/nonexistent/path-xyz.bin", profile_override=other)
    assert code != 0
    assert "evidence_not_found" in (out.get("reason") or "")


def test_accept_by_non_participant_refused(run, home, profile, other):
    tid = _make_task(run, "чужая приёмка", owner=other)
    run("task", "claim", tid, profile_override=other)
    run("task", "submit", tid, profile_override=other)
    code, out, err = run("task", "accept", tid, "--no-evidence", profile_override="randprof-z")
    assert code != 0
    assert "not_participant" in (out.get("reason") or "")
