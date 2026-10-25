"""Task objects as first-class bus records (DESIGN-A2A-V05 §1, v0.5.0).

Storage: `tasks/<id>.md` — front matter + spec body, same atomic pipeline as
mail/journal. Transitions are validated by a state machine; terminal
states (done/rejected) require evidence (artifact path or journal id),
bypassable only with an explicit `--no-evidence` that stamps the record
`unverified` — in the object itself and in the user-feed note.

Every status change auto-writes a linked journal entry (bidirectional
task_id <-> journal id) and the whole set of files lands through
util.commit_bundle, so a crash mid-transition is healed by `a2a doctor`.
"""

from __future__ import annotations

import json
import re
import time
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import access, auditlog, journal, userfeed, schema, scrubber, util

VALID_STATUSES = {
    "backlog", "assigned", "in_progress", "review",
    "done", "rejected", "blocked", "cancelled",
}
TERMINAL_STATUSES = {"done", "rejected", "cancelled"}
VALID_PRIORITIES = {"fyi", "today", "critical"}

# command -> (allowed source statuses, target status)
TRANSITIONS: dict[str, tuple[set[str], str]] = {
    "claim":  ({"backlog", "assigned", "blocked"}, "in_progress"),
    "submit": ({"in_progress"}, "review"),
    "accept": ({"review"}, "done"),
    "reject": ({"review"}, "rejected"),
    "block":  ({"backlog", "assigned", "in_progress", "review"}, "blocked"),
    "cancel": (VALID_STATUSES - TERMINAL_STATUSES, "cancelled"),
}

_ID_RE = re.compile(r"^[0-9a-f]{26,40}$")


def tasks_dir() -> Path:
    return util.HOME / "tasks"


def task_path(id_: str) -> Path:
    return tasks_dir() / f"{id_}.md"


def _valid_id(id_: str) -> bool:
    return bool(_ID_RE.fullmatch(str(id_ or "")))


def _load(id_: str, profile: str | None = None, include_l1: bool = False) -> tuple[Path, dict[str, Any]]:
    if not _valid_id(id_):
        util.fail_hint(util.EXIT_USAGE_ERROR, "task_not_found", hint="задача с таким id не найдена — проверь a2a task list", example="a2a task list", data={"id": id_})
    path = task_path(id_)
    rec = util.read_record(path) if path.is_file() else None
    if not rec or rec.get("type") != "task":
        # do not leak existence of records the caller may not read
        util.fail_hint(util.EXIT_USAGE_ERROR, "task_not_found", hint="задача с таким id не найдена — проверь a2a task list", example="a2a task list", data={"id": id_})
    if profile is not None and not _readable(rec, profile, include_l1):
        util.fail_hint(util.EXIT_USAGE_ERROR, "task_not_found", hint="задача с таким id не найдена — проверь a2a task list", example="a2a task list", data={"id": id_})
    return path, rec


def _readable(rec: dict[str, Any], profile: str, include_l1: bool = False) -> bool:
    allowed = access.may_read(rec, profile, include_l1)
    if allowed and include_l1 and rec.get("access") == "L1" and rec.get("author") != profile:
        acl = rec.get("acl")
        if not (isinstance(acl, list) and acl):
            auditlog.log("task_l1_read", profile, str(rec.get("author")), {"id": rec.get("id")})
    return allowed


def _participants(rec: dict[str, Any]) -> set[str]:
    names = {str(rec.get("author") or ""), str(rec.get("owner") or rec.get("author") or "")}
    history = rec.get("assignee_history") or []
    if history and isinstance(history[-1], dict) and history[-1].get("to"):
        names.add(str(history[-1]["to"]))
    return {n for n in names if n}


def _require_participant(rec: dict[str, Any], profile: str, cmd: str, as_owner: bool = False) -> None:
    """v0.6.0 §2 (F-01): write-scope. Переход/правка чужой задачи без
    owner/assignee/author — invalid_actor с подсказкой; явный обход --as-owner
    пишется в аудит событием write_scope_override (--force не добавляем)."""
    if profile in _participants(rec):
        return
    if as_owner:
        auditlog.log("write_scope_override", profile, str(rec.get("id")),
                     {"cmd": cmd, "status": rec.get("status")})
        return
    util.fail_hint(
        util.EXIT_ERROR,
        "invalid_actor:not_participant",
        "только участники (author/owner/последний assignee); явный админ-обход: --as-owner (пишет аудит write_scope_override)",
        "a2a task " + str(cmd) + " " + str(rec.get("id")) + " --as-owner",
        {
            "task": rec.get("id"),
            "cmd": cmd,
            "actor_fields": ["author", "owner", "assignee_history[-1].to"],
        },
    )


def _norm_deadline(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d")
    except Exception:
        pass
    try:
        return util.parse_utc(raw).strftime("%Y-%m-%d")
    except Exception:
        util.fail(util.EXIT_INVALID_RECORD, "invalid_deadline", {"value": raw})
    return None  # unreachable


def _journal_exists(jid: str) -> bool:
    base = util.HOME / "journal"
    if not jid or not base.is_dir():
        return False
    return bool(list(base.rglob(f"*_{jid}.md")))


def _mail_exists(mid: str) -> bool:
    base = util.HOME / "mail"
    if not mid or not base.is_dir():
        return False
    return any(base.rglob(f"*_{mid}.md"))


def _resolve_evidence(ref: str) -> dict[str, Any]:
    """Evidence = journal id (exists) or artifact path (exists). Else fail."""
    ref = str(ref or "").strip()
    if _ID_RE.fullmatch(ref):
        if _journal_exists(ref) or _mail_exists(ref):
            return {"kind": "journal", "ref": ref, "at": util.utc_iso()}
        util.fail(util.EXIT_INVALID_RECORD, "evidence_not_found", {"ref": ref, "hint": "no journal/mail record with this id"})
    p = Path(ref).expanduser()
    if p.exists():
        return {"kind": "artifact", "ref": str(p), "at": util.utc_iso()}
    util.fail(util.EXIT_INVALID_RECORD, "evidence_not_found", {"ref": ref})
    return {}  # unreachable


def _revalidate(evidence: list[dict[str, Any]]) -> bool:
    """All stored evidence still resolves right now."""
    for e in evidence or []:
        if e.get("kind") == "artifact":
            if not Path(str(e.get("ref", ""))).exists():
                return False
        elif not (_journal_exists(str(e.get("ref", ""))) or _mail_exists(str(e.get("ref", "")))):
            return False
    return bool(evidence)


def _render_body(rec: dict[str, Any]) -> str:
    lines = [f"# {rec.get('title', '')}", ""]
    goal = rec.get("goal")
    if goal:
        lines += [f"Goal: {goal}", ""]
    acceptance = rec.get("acceptance") or []
    if acceptance:
        lines.append("Acceptance:")
        lines += [f"- {a}" for a in acceptance]
        lines.append("")
    return "\n".join(lines)


def _serialize(path: Path, rec: dict[str, Any]) -> str:
    meta = {k: v for k, v in rec.items() if not k.startswith("_")}
    return util.join_front_matter(meta, rec.get("_body", ""))


def _check_secret(text: str) -> None:
    dirty, hits = scrubber.check(text)
    if dirty:
        util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", 'a2a task new "title without secrets"', {"hits": hits})


def _scrub_record(rec: dict[str, Any], path: Path, profile: str, channel: str) -> None:
    """v0.6.0 §1: task spec body — redact-канал. UNREDACTABLE (PEM/PGP/url-secret)
    остаётся hard-reject; остальное редактируется в полях записи + аудит-событие.
    Redact пофилдовый (до сериализации): redact по готовому yaml съедал кавычки."""
    text = _serialize(path, rec)
    hits = scrubber.scan(text)
    if not hits:
        return
    hs = set(hits)
    if hs & scrubber.UNREDACTABLE:
        try:
            from . import quarantine as _q
            _q.quarantine_payload(profile, text, hits)
        except Exception:
            pass
        util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", 'a2a task new "title without secrets"', {"hits": hits})
    cleaned = scrubber.scrub_value({k: v for k, v in rec.items()})
    rec.clear()
    rec.update(cleaned)
    residual = scrubber.scan(_serialize(path, rec))
    if residual:
        try:
            from . import quarantine as _q
            _q.quarantine_payload(profile, text, residual)
        except Exception:
            pass
        util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", 'a2a task new "title without secrets"', {"hits": sorted(set(residual))})
    auditlog.log("secret_redacted", profile, channel,
                 {"hits": sorted(hs), "id": rec.get("id")})


def _transition_journal_note(actor: str, rec: dict[str, Any], verb: str, artifacts: list[str]) -> dict[str, Any]:
    """Deferred journal entry for one status change (auto, poka-yoke bypassed
    for system-generated lines — the task record itself holds the claims)."""
    tid = rec.get("id")
    title = rec.get("title", "")
    note = Namespace(
        what_done=f"task {tid}: {verb} — {title}".strip(),
        left=None,
        artifacts=",".join(artifacts) if artifacts else None,
        deadline=None,
        verify=None,
        verify_run=False,
        numbers_ok=True,
        status=None,
        session=None,
        task_id=tid,
        access=None,
        profile=actor,
        as_=None,
    )
    return journal.write_entry(note, defer=True)


def _user_terminal_note(actor: str, rec: dict[str, Any], unverified: bool) -> dict[str, Any]:
    status = rec.get("status")
    marker = " [UNVERIFIED: принята без доказательств (--no-evidence)]" if unverified else ""
    ev = rec.get("evidence") or []
    ev_txt = "; ".join(str(e.get("ref")) for e in ev) if ev else "нет"
    body = (
        f"task {rec.get('id')} {status}: {rec.get('title')} "
        f"(owner {rec.get('owner')}, evidence: {ev_txt}){marker}"
    )
    if unverified:
        body += "\nunverified evidence gate — см. запись задачи."
    return userfeed.write_note(actor, body, kind="fyi", defer=True)


def _commit_task_transition(path: Path, rec: dict[str, Any], journal_resp: dict[str, Any],
                            user_resp: dict[str, Any] | None, label: str) -> None:
    ops: list[tuple[Path, str]] = [(path, _serialize(path, rec)), (Path(journal_resp["path"]), journal_resp["text"])]
    if user_resp is not None:
        ops.append((Path(user_resp["path"]), user_resp["text"]))
    try:
        util.commit_bundle(ops, label=label)
    except OSError as exc:
        util.fail(util.EXIT_ERROR, "bundle_commit_failed", {"error": str(exc), "hint": "a2a doctor recovers"})
    if user_resp is not None:
        userfeed.mirror_to_feed(Path(user_resp["path"]), user_resp["text"])


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_task_new(args: Namespace, profile: str) -> None:
    start = time.time()
    access.enforce_known(profile, "author")
    owner = args.owner or profile
    access.enforce_known(owner, "owner")
    title = (args.title or "").strip()
    if not title:
        util.fail(util.EXIT_USAGE_ERROR, "empty_title")
    priority = args.priority or "today"
    if priority not in VALID_PRIORITIES:
        util.fail(util.EXIT_INVALID_RECORD, "invalid_priority", {"value": priority})
    deadline = _norm_deadline(args.deadline)
    parent_id = None
    if args.parent:
        # ids are opaque tokens: validate shape before touching the filesystem
        if not _valid_id(args.parent) or not task_path(args.parent).is_file():
            util.fail(util.EXIT_USAGE_ERROR, "unknown_parent", {"parent": args.parent})
        parent_id = args.parent
    access_level = getattr(args, "access", None) or schema.DEFAULT_ACCESS.get("task", "L2")
    if access_level not in schema.ACCESS_LEVELS:
        util.fail(util.EXIT_INVALID_RECORD, "invalid_access", {"value": access_level})
    acl = None
    if getattr(args, "acl", None):
        acl = [t.strip() for t in str(args.acl).split(",") if t.strip()]

    idem_key = getattr(args, "idempotency_key", None)
    if idem_key:
        from . import idempotency
        prior = idempotency.lookup("task_new", idem_key)
        if prior:
            util.ok(
                {"id": prior, "duplicate_suppressed": True, "status": "ok"},
                extra_metadata={"duplicate_suppressed": True},
            )

    status = "assigned" if owner != profile else "backlog"
    now = util.utc_iso()
    rec: dict[str, Any] = {
        "type": "task",
        "id": util.new_id(),
        "author": profile,
        "owner": owner,
        "access": access_level,
        "status": status,
        "title": title,
        "goal": args.goal or None,
        "acceptance": list(args.acceptance or []),
        "priority": priority,
        "deadline": deadline,
        "parent_id": parent_id,
        "links": {"journal": [], "mail": [], "artifacts": []},
        "assignee_history": [{"from": None, "to": owner, "at": now, "reason": "created"}],
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "evidence": [],
        "_body": "",
    }
    if acl:
        rec["acl"] = acl
    rec["_body"] = _render_body(rec)
    jid_resp = _transition_journal_note(profile, rec, "created", [])
    rec["links"]["journal"].append(jid_resp["id"])
    rec["_body"] = _render_body(rec)
    _scrub_record(rec, task_path(rec["id"]), profile, "task_spec")

    if idem_key:
        from . import idempotency
        idempotency.reserve("task_new", idem_key, rec["id"])

    _commit_task_transition(task_path(rec["id"]), rec, jid_resp, None, label=f"task:{rec['id']}:new")
    # v0.6.0 §3 (H-01): operate-аудит state-changing операций
    auditlog.log("task_new", profile, rec["id"], {"status": status, "owner": owner})
    if idem_key:
        from . import idempotency
        idempotency.confirm("task_new", idem_key, rec["id"])
    util.ok(
        {"id": rec["id"], "status": status, "owner": owner, "title": title,
         "path": str(task_path(rec["id"])), "journal_id": jid_resp["id"]},
        elapsed_ms=int((time.time() - start) * 1000),
    )


def _apply_transition(args: Namespace, profile: str, cmd: str) -> None:
    start = time.time()
    access.enforce_known(profile, "caller")
    path, rec = _load(args.id, profile, include_l1=getattr(args, "include_l1", False))
    # v0.6.0 §6 (J-06): CAS — явный --expect-version сверяется ДО любых мутаций;
    # без флага поведение побайтово как v0.5.0 (обратная совместимость).
    expect_version = getattr(args, "expect_version", None)
    if expect_version is not None:
        try:
            want_v = int(expect_version)
        except (TypeError, ValueError):
            util.fail(util.EXIT_USAGE_ERROR, "invalid_expect_version", {"value": str(expect_version)})
        actual_v = int(rec.get("version", 0))
        if want_v != actual_v:
            util.fail_hint(util.EXIT_ERROR, "stale_version",
                          "version mismatch; re-read task and retry with current --expect-version",
                          "a2a task " + str(cmd) + " " + str(rec.get("id")) + " --expect-version " + str(actual_v),
                          {"task": rec.get("id"), "expected": want_v, "actual": actual_v})
    allowed_sources, target = TRANSITIONS[cmd]
    current = str(rec.get("status"))
    if current not in allowed_sources:
        util.fail(
            util.EXIT_INVALID_RECORD,
            "invalid_transition",
            {"task": rec.get("id"), "status": current, "attempted": f"{cmd}: {current} -> {target}"},
        )
    reason = getattr(args, "reason", None)
    if cmd in ("block", "reject") and not (reason or "").strip():
        util.fail(util.EXIT_USAGE_ERROR, "reason_required", {"cmd": cmd})
    if cmd in ("claim", "submit", "accept", "reject", "block", "cancel"):
        # v0.6.0 §2: claim тоже в write-scope — раньше чужой claim проходил
        _require_participant(rec, profile, cmd, as_owner=bool(getattr(args, "as_owner", False)))

    actor = profile
    evidence = list(rec.get("evidence") or [])
    no_evidence = bool(getattr(args, "no_evidence", False))
    unverified = False
    evidence_verified: bool | None = None

    new_refs = list(getattr(args, "evidence", None) or [])
    for ref in new_refs:
        evidence.append(_resolve_evidence(ref))

    if target in TERMINAL_STATUSES and target in ("done", "rejected"):
        if not evidence and not no_evidence:
            util.fail(util.EXIT_INVALID_RECORD, "evidence_required",
                      {"task": rec.get("id"), "hint": "submit --evidence <path|journal-id>, или accept --no-evidence (пометит unverified)"})
        if no_evidence or not _revalidate(evidence):
            unverified = True
        evidence_verified = not unverified

    if cmd == "claim":
        prev = (rec.get("assignee_history") or [{}])[-1].get("to")
        rec.setdefault("assignee_history", []).append(
            {"from": prev, "to": actor, "at": util.utc_iso(), "reason": reason or "claim"}
        )
        rec["blocked_reason"] = None
    if cmd == "block":
        rec["blocked_reason"] = reason
    if cmd == "reject":
        rec["reject_reason"] = reason
    if cmd == "cancel":
        rec["cancel_reason"] = reason
    if cmd == "submit":
        rec["evidence"] = evidence
    if target in ("done", "rejected"):
        rec["evidence"] = evidence
        rec["evidence_verified"] = bool(evidence_verified)
        if unverified:
            rec["unverified"] = True
    rec["status"] = target
    rec["version"] = int(rec.get("version", 0)) + 1
    rec["updated_at"] = util.utc_iso()
    rec["_body"] = _render_body(rec)

    artifacts_for_journal = [str(e.get("ref")) for e in evidence if e.get("kind") == "artifact"]
    jid_resp = _transition_journal_note(actor, rec, cmd, artifacts_for_journal)
    rec.setdefault("links", {"journal": [], "mail": [], "artifacts": []})
    rec["links"].setdefault("journal", []).append(jid_resp["id"])
    rec["_body"] = _render_body(rec)

    user_resp = None
    if target in ("done", "rejected"):
        user_resp = _user_terminal_note(actor, rec, unverified)

    _scrub_record(rec, path, actor, "task_spec")

    cascaded: list[str] = []
    if cmd == "cancel" and getattr(args, "cascade", "block") == "block":
        cascaded = _cascade_children(args, rec, actor)

    _commit_task_transition(path, rec, jid_resp, user_resp, label=f"task:{rec['id']}:{cmd}")
    # v0.6.0 §3: task_* переходы в operate-аудит (state-changing только после
    # успешного коммита — неудачный переход не оставляет события)
    auditlog.log(f"task_{cmd}", actor, rec["id"],
                 {"from": current, "to": target, "version": rec["version"]})
    if cmd == "cancel" and cascaded:
        auditlog.log("task_cancel_cascade", actor, rec["id"], {"children": cascaded})
    response: dict[str, Any] = {
        "id": rec["id"], "status": target, "version": rec["version"],
        "journal_id": jid_resp["id"],
    }
    if target in ("done", "rejected"):
        response["evidence_verified"] = bool(evidence_verified)
        response["unverified"] = unverified
    if cmd == "cancel":
        response["cascaded"] = cascaded
    util.ok(response, elapsed_ms=int((time.time() - start) * 1000))


def _cascade_children(args: Namespace, parent: dict[str, Any], actor: str) -> list[str]:
    """Children of a cancelled task -> blocked: parent_cancelled. Retain, not delete."""
    pid = parent["id"]
    cascaded: list[str] = []
    for p in sorted(util.list_files(tasks_dir())):
        rec = util.read_record(p)
        if not rec or rec.get("type") != "task" or rec.get("parent_id") != pid:
            continue
        if str(rec.get("status")) in TERMINAL_STATUSES:
            continue
        rec["status"] = "blocked"
        rec["blocked_reason"] = "parent_cancelled"
        rec["version"] = int(rec.get("version", 0)) + 1
        rec["updated_at"] = util.utc_iso()
        rec["_body"] = _render_body(rec)
        jid_resp = _transition_journal_note(actor, rec, "cancel-cascade", [])
        rec.setdefault("links", {"journal": [], "mail": [], "artifacts": []})
        rec["links"].setdefault("journal", []).append(jid_resp["id"])
        rec["_body"] = _render_body(rec)
        cascaded.append(rec["id"])
        _commit_task_transition(p, rec, jid_resp, None, label=f"task:{rec['id']}:cancel-cascade")
    return cascaded


def cmd_task_show(args: Namespace, profile: str) -> None:
    start = time.time()
    _, rec = _load(args.id, profile, include_l1=getattr(args, "include_l1", False))
    is_participant = profile in _participants(rec)
    data: dict[str, Any] = {}
    for key in ("id", "type", "author", "owner", "access", "status", "title",
                "priority", "deadline", "parent_id", "created_at", "updated_at",
                "version", "blocked_reason", "reject_reason", "cancel_reason",
                "evidence", "evidence_verified", "unverified", "acl"):
        if key in rec:
            data[key] = rec[key]
    if is_participant:
        data["goal"] = rec.get("goal")
        data["acceptance"] = rec.get("acceptance") or []
        data["links"] = rec.get("links")
        data["assignee_history"] = rec.get("assignee_history") or []
        data["body"] = rec.get("_body", "")
        data["restricted"] = False
    else:
        # DESIGN §4: default L2 for other profiles in board/list — summary,
        # details only for participants.
        data["restricted"] = True
    util.ok(data, elapsed_ms=int((time.time() - start) * 1000))


def _iter_visible_tasks(profile: str, include_l1: bool = False) -> list[dict[str, Any]]:
    out = []
    for p in sorted(util.list_files(tasks_dir())):
        rec = util.read_record(p)
        if not rec or rec.get("type") != "task":
            continue
        if not _readable(rec, profile, include_l1):
            continue
        out.append(rec)
    return out


def _is_overdue(rec: dict[str, Any], today: str) -> bool:
    dl = rec.get("deadline")
    return bool(dl) and str(dl) < today and str(rec.get("status")) not in TERMINAL_STATUSES


def _task_flags(rec: dict[str, Any], today: str) -> list[str]:
    flags = []
    if _is_overdue(rec, today):
        flags.append("overdue")
    if rec.get("unverified"):
        flags.append("unverified")
    if rec.get("blocked_reason"):
        flags.append(f"blocked:{rec['blocked_reason']}")
    if rec.get("parent_id"):
        flags.append("child")
    return flags




def _is_overdue(rec: dict[str, Any], today: str) -> bool:
    dl = str(rec.get("deadline") or "")
    if not dl or str(rec.get("status")) in TERMINAL_STATUSES:
        return False
    return dl < today


def _sla_breach(rec: dict[str, Any], hours: int = 0) -> bool:
    dl = str(rec.get("deadline") or "")
    if not dl or str(rec.get("status")) in TERMINAL_STATUSES:
        return False
    try:
        # deadline date end-of-day UTC
        from datetime import datetime, timezone, timedelta
        deadline_dt = datetime.strptime(dl, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        overdue = (util.now_utc() - deadline_dt).total_seconds() / 3600.0
        return overdue > float(hours)
    except Exception:
        try:
            return dl < util.now_utc().date().isoformat()
        except Exception:
            return False


def _sla_notify_once(rec: dict[str, Any], caller: str) -> None:
    try:
        from . import dnd_policy
        policy, warning, missing = dnd_policy.read_policy()
        if missing:
            return
    except Exception:
        return
    log_path = util.HOME / ".state" / "sla_sent.log"
    try:
        util.ensure_dir(log_path)
    except Exception:
        pass
    tid = str(rec.get("id"))
    if log_path.exists():
        try:
            for line in log_path.read_text(encoding="utf-8").splitlines():
                line=line.strip()
                if not line:
                    continue
                if tid in line:
                    return
        except Exception:
            pass
    owner = str(rec.get("owner") or rec.get("author") or caller)
    # send critical mail
    try:
        from . import mail as _mail
        from argparse import Namespace as _NS
        send_args = _NS(
            to=owner,
            subject=f"SLA breach: {rec.get('title')}",
            body=f"Task {tid} overdue (deadline {rec.get('deadline')})",
            priority="critical",
            ref=tid,
            access=None,
            profile=caller,
            as_=None,
        )
        for attr in ("numbers_ok", "verify", "verify_run", "idempotency_key", "tags", "attach"):
            setattr(send_args, attr, (False if attr=="numbers_ok" else None) if attr!="verify_run" else False)
        send_args.numbers_ok = True
        _mail.cmd_mail_send(send_args)
    except Exception:
        pass
    try:
        import json as _json
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(_json.dumps({"task_id": tid, "ts": util.utc_iso()}, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass



def cmd_task_list(args: Namespace, profile: str) -> None:
    start = time.time()
    today = util.now_utc().date().isoformat()
    status_filter = getattr(args, "status", None)
    if status_filter and status_filter not in VALID_STATUSES:
        util.fail(util.EXIT_USAGE_ERROR, "invalid_status", {"value": status_filter})
    owner_filter = getattr(args, "owner", None)
    overdue_only = bool(getattr(args, "overdue", False))
    sla = bool(getattr(args, "sla", False))
    sla_hours = int(getattr(args, "sla_hours", 0) or 0)
    entries = []
    sla_breach_any = False
    for rec in _iter_visible_tasks(profile, include_l1=getattr(args, "include_l1", False)):
        if owner_filter and rec.get("owner") != owner_filter:
            continue
        if status_filter and rec.get("status") != status_filter:
            continue
        if overdue_only and not _is_overdue(rec, today):
            continue
        breach = _sla_breach(rec, sla_hours)
        if sla and breach:
            sla_breach_any = True
            _sla_notify_once(rec, profile)
        entry = {
            "id": rec["id"],
            "title": rec.get("title"),
            "status": rec.get("status"),
            "owner": rec.get("owner"),
            "author": rec.get("author"),
            "priority": rec.get("priority"),
            "deadline": rec.get("deadline"),
            "updated_at": rec.get("updated_at"),
            "flags": _task_flags(rec, today),
            "sla_breach": breach,
        }
        entries.append(entry)
    out: dict[str, Any] = {"count": len(entries), "entries": entries}
    if sla:
        out["sla"] = True
        out["sla_breach"] = sla_breach_any
        out["sla_breach_count"] = sum(1 for e in entries if e.get("sla_breach"))
    util.ok(out, elapsed_ms=int((time.time() - start) * 1000))


def overdue_summary(profile: str) -> dict[str, Any]:
    """Read-only task stats for `mail check` (no new cron — DESIGN §1)."""
    today = util.now_utc().date().isoformat()
    overdue: list[dict[str, Any]] = []
    due_today: list[dict[str, Any]] = []
    for rec in _iter_visible_tasks(profile):
        dl = str(rec.get("deadline") or "")
        if str(rec.get("status")) in TERMINAL_STATUSES or not dl:
            continue
        item = {"id": rec["id"], "title": rec.get("title"), "deadline": dl,
                "owner": rec.get("owner"), "priority": rec.get("priority")}
        if dl < today:
            overdue.append(item)
        elif dl == today:
            due_today.append(item)
    return {"overdue": len(overdue), "due_today": len(due_today),
            "overdue_items": overdue[:10], "due_today_items": due_today[:10]}


def cmd_task_board(args: Namespace, profile: str) -> None:
    start = time.time()
    today = util.now_utc().date().isoformat()
    recs = _iter_visible_tasks(profile, include_l1=getattr(args, "include_l1", False))
    # kanban-срез по профилям-владельцам; user-safe: чужие L1 уже отфильтрованы
    by_owner: dict[str, list[dict[str, Any]]] = {}
    for rec in recs:
        by_owner.setdefault(str(rec.get("owner") or "?"), []).append(rec)
    lines = [f"# a2a task board — caller={profile} UTC={today}"]
    sections = []
    for owner in sorted(by_owner):
        group = by_owner[owner]
        lines.append("")
        lines.append(f"## owner: {owner} ({len(group)})")
        lines.append("| task | status | priority | deadline | flags | title |")
        lines.append("|------|--------|----------|----------|-------|-------|")
        ids = []
        for rec in sorted(group, key=lambda r: str(r.get("deadline") or "9999-99-99")):
            flags = ",".join(_task_flags(rec, today))
            safe_title = str(rec.get("title") or "").replace("|", "\\|")
            lines.append(
                f"| {str(rec.get('id'))[:8]} | {rec.get('status')} | {rec.get('priority')} "
                f"| {rec.get('deadline') or '—'} | {flags or '—'} | {safe_title} |"
            )
            ids.append(rec.get("id"))
        sections.append({"owner": owner, "ids": ids})
    markdown = "\n".join(lines) + "\n"
    util.ok(
        {"markdown": markdown, "count": len(recs), "sections": sections},
        elapsed_ms=int((time.time() - start) * 1000),
    )


def cmd_task_edit(args: Namespace, profile: str) -> None:
    start = time.time()
    access.enforce_known(profile, "caller")
    path, rec = _load(args.id, profile, include_l1=getattr(args, "include_l1", False))
    _require_participant(rec, profile, "edit", as_owner=bool(getattr(args, "as_owner", False)))

    # v1.0.0 §6 J-06: CAS — сверка ДО любых мутаций.
    expect_version = getattr(args, "expect_version", None)
    if expect_version is not None:
        try:
            want_v = int(expect_version)
        except (TypeError, ValueError):
            util.fail(util.EXIT_USAGE_ERROR, "invalid_expect_version", {"value": str(expect_version)})
        actual_v = int(rec.get("version", 0))
        if want_v != actual_v:
            util.fail_hint(util.EXIT_ERROR, "stale_version",
                          "version mismatch; re-read task and retry with current --expect-version",
                          "a2a task edit " + str(rec.get("id")) + " --expect-version " + str(actual_v),
                          {"task": rec.get("id"), "expected": want_v, "actual": actual_v})

    changed = False
    if getattr(args, "title", None) is not None:
        rec["title"] = args.title
        changed = True
    if getattr(args, "goal", None) is not None:
        rec["goal"] = args.goal
        changed = True
    if getattr(args, "owner", None) is not None:
        rec["owner"] = args.owner
        changed = True
    if getattr(args, "deadline", None) is not None:
        rec["deadline"] = _norm_deadline(args.deadline)
        changed = True
    if getattr(args, "priority", None) is not None:
        rec["priority"] = args.priority
        changed = True
    if getattr(args, "acceptance", None):
        rec["acceptance"] = list(args.acceptance)
        changed = True

    # always bump version on a successful edit (CAS contract), even if no
    # field changed — the caller explicitly asked to edit.
    rec["version"] = int(rec.get("version", 0)) + 1
    rec["updated_at"] = util.utc_iso()
    rec["_body"] = _render_body(rec)

    _scrub_record(rec, path, profile, "task_spec")

    util.write_atomically(path, _serialize(path, rec))
    auditlog.log("task_edit", profile, rec["id"], {"version": rec["version"]})

    util.ok({"id": rec["id"], "version": rec["version"], "status": rec.get("status"),
             "changed": changed}, elapsed_ms=int((time.time() - start) * 1000))


def cmd_task_link(args: Namespace, profile: str) -> None:
    start = time.time()
    path, rec = _load(args.id, profile)
    _require_participant(rec, profile, "link", as_owner=bool(getattr(args, "as_owner", False)))
    # --ref generic: resolve to journal/mail by existence
    for ref in getattr(args, "ref", None) or []:
        if _journal_exists(ref):
            args.journal = list(getattr(args, "journal", None) or []) + [ref]
            # avoid double processing: clear ref after? We'll handle by skipping refs later
        elif _mail_exists(ref):
            args.mail = list(getattr(args, "mail", None) or []) + [ref]
        else:
            util.fail(util.EXIT_USAGE_ERROR, "record_not_found", {"ref": ref})
    # dedupe not necessary
    links = rec.setdefault("links", {"journal": [], "mail": [], "artifacts": []})
    added: dict[str, list[str]] = {}
    for ref in getattr(args, "journal", None) or []:
        if not _journal_exists(ref):
            util.fail(util.EXIT_USAGE_ERROR, "journal_not_found", {"ref": ref})
        if ref not in links.setdefault("journal", []):
            links["journal"].append(ref)
            added.setdefault("journal", []).append(ref)
        # backlink into the journal record (обратная сторона link)
        for jp in (util.HOME / "journal").rglob(f"*_{ref}.md"):
            jrec = util.read_record(jp)
            if jrec:
                task_ids = list(jrec.get("task_ids") or [])
                if rec["id"] not in task_ids:
                    task_ids.append(rec["id"])
                jrec["task_ids"] = task_ids
                jrec["task_id"] = rec["id"]
                jrec.pop("_path", None)
                body = jrec.pop("_body", "")
                util.write_atomically(jp, util.join_front_matter(jrec, body))
    for ref in getattr(args, "mail", None) or []:
        if not _mail_exists(ref):
            util.fail(util.EXIT_USAGE_ERROR, "mail_not_found", {"ref": ref})
        if ref not in links.setdefault("mail", []):
            links["mail"].append(ref)
            added.setdefault("mail", []).append(ref)
        # backlink into the mail record(s) referencing this task
        for mp in (util.HOME / "mail").rglob(f"*_{ref}.md"):
            mrec = util.read_record(mp)
            if mrec:
                task_ids = list(mrec.get("task_ids") or [])
                if rec["id"] not in task_ids:
                    task_ids.append(rec["id"])
                mrec["task_ids"] = task_ids
                mrec["task_id"] = rec["id"]
                mrec.pop("_path", None)
                body = mrec.pop("_body", "")
                util.write_atomically(mp, util.join_front_matter(mrec, body))
    for ref in getattr(args, "artifact", None) or []:
        if ref not in links.setdefault("artifacts", []):
            links["artifacts"].append(str(ref))
            added.setdefault("artifacts", []).append(str(ref))
    rec["version"] = int(rec.get("version", 0)) + 1
    rec["updated_at"] = util.utc_iso()
    rec["_body"] = _render_body(rec)
    _scrub_record(rec, path, profile, "task_spec")
    util.write_atomically(path, _serialize(path, rec))
    util.ok({"id": rec["id"], "links": links, "added": added},
            elapsed_ms=int((time.time() - start) * 1000))


def _escalated_log_path() -> Path:
    return util.HOME / ".state" / "escalated.log"


def _escalation_already_logged(task_id: str, day: str) -> bool:
    path = _escalated_log_path()
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            # tolerant: allow "task_id day" plain lines
            parts = line.split()
            if len(parts) >= 2 and parts[0] == task_id and parts[1] == day:
                return True
            continue
        if str(entry.get("task_id") or entry.get("id") or "") == str(task_id) and str(entry.get("date") or entry.get("day") or "") == day:
            return True
    return False


def _log_escalation(task_id: str, day: str, severity: str, recipients: list[str]) -> None:
    path = _escalated_log_path()
    util.ensure_dir(path)
    entry = {"task_id": task_id, "date": day, "severity": severity,
             "recipients": recipients, "ts": util.utc_iso()}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _assignee_of(rec: dict[str, Any]) -> str | None:
    hist = rec.get("assignee_history") or []
    if hist and isinstance(hist[-1], dict) and hist[-1].get("to"):
        return str(hist[-1]["to"])
    return rec.get("owner") or rec.get("author")


def _send_escalation_mail(profile: str, recipient: str, subject: str, body: str, priority: str) -> str:
    meta = {
        "type": "mail",
        "author": profile,
        "to": recipient,
        "access": "L2",
        "subject": subject,
        "msg_status": "unread",
        "priority": priority,
    }
    schema.apply_defaults(meta, "mail")
    # schema only allows fyi/today; keep escalation mail deliverable
    if meta.get("priority") not in ("fyi", "today"):
        meta["priority"] = "today"
    text = util.join_front_matter(meta, body)
    okv, err = schema.validate(meta, body)
    if not okv:
        raise RuntimeError(err or "invalid mail")
    created = util.parse_utc(meta["created_at"])
    mid = meta["id"]
    out_path = util.HOME / "mail" / profile / "outbox" / f"{created.strftime('%Y-%m-%d')}_{mid}.md"
    in_path = util.HOME / "mail" / recipient / "inbox" / f"{created.strftime('%Y-%m-%d')}_{mid}.md"
    util.commit_bundle([(out_path, text), (in_path, text)], label=f"escalate:{mid}")
    return mid


def cmd_task_escalate(args: Namespace, profile: str) -> None:
    start = time.time()
    task_id = getattr(args, "due", None) or getattr(args, "id", None)
    # scan mode: escalate all overdue visible tasks
    if not task_id:
        escalated_ids = []
        for rec in _iter_visible_tasks(profile, include_l1=getattr(args, "include_l1", False)):
            if str(rec.get("status")) not in ("backlog", "assigned", "in_progress"):
                continue
            if not _is_overdue(rec, util.now_utc().date().isoformat()):
                continue
            sub_args = Namespace(**{k: getattr(args, k, None) for k in ("include_l1",)})
            sub_args.due = rec.get("id")
            sub_args.id = rec.get("id")
            sub_args.profile = getattr(args, "profile", None)
            sub_args.as_ = getattr(args, "as_", None)
            try:
                cmd_task_escalate(sub_args, profile)
                escalated_ids.append(rec.get("id"))
            except SystemExit:
                pass
        util.ok({"escalated": escalated_ids, "count": len(escalated_ids)},
               elapsed_ms=int((time.time() - start) * 1000))
        return

    path, rec = _load(task_id, profile, include_l1=getattr(args, "include_l1", False))
    status = str(rec.get("status"))
    deadline = rec.get("deadline")
    today = util.now_utc().date().isoformat()

    if status not in ("backlog", "assigned", "in_progress"):
        util.ok({"id": task_id, "escalated": False, "reason": "status_not_escalatable",
                 "status": status}, elapsed_ms=int((time.time() - start) * 1000))
        return
    if not deadline:
        util.ok({"id": task_id, "escalated": False, "reason": "no_deadline",
                 "status": status}, elapsed_ms=int((time.time() - start) * 1000))
        return
    if not _is_overdue(rec, today):
        util.ok({"id": task_id, "escalated": False, "reason": "not_overdue",
                 "deadline": deadline, "status": status},
               elapsed_ms=int((time.time() - start) * 1000))
        return

    # severity: >48h overdue -> critical, else warning
    try:
        dl_dt = util.parse_utc(str(deadline))
    except Exception:
        dl_dt = util.parse_utc(str(deadline) + "T00:00:00Z")
    overdue_hours = (util.now_utc() - dl_dt).total_seconds() / 3600.0
    severity = "critical" if overdue_hours > 48 else "warning"
    mail_priority = "today"

    day = util.now_utc().date().isoformat()
    if _escalation_already_logged(task_id, day):
        util.ok({"id": task_id, "escalated": False, "deduplicated": True,
                 "duplicate": True, "duplicate_suppressed": True, "severity": severity,
                 "mail_ids": [], "reason": "duplicate_window"},
               elapsed_ms=int((time.time() - start) * 1000))
        return

    owner = str(rec.get("owner") or rec.get("author") or profile)
    assignee = _assignee_of(rec)
    recipients = []
    for r in (owner, assignee):
        if r and r not in recipients:
            recipients.append(r)

    subject = f"[{severity.upper()}] overdue task {task_id}: {rec.get('title')}"
    body = (f"Task {task_id} is overdue (deadline {deadline}, ~{overdue_hours:.1f}h). "
            f"Status: {status}. Severity: {severity}. Escalated manually.")

    mail_ids = []
    for recipient in recipients:
        try:
            mid = _send_escalation_mail(profile, recipient, subject, body, mail_priority)
            mail_ids.append(mid)
        except Exception as exc:
            # best-effort per recipient
            pass

    _log_escalation(task_id, day, severity, recipients)
    auditlog.log("task_escalate", profile, task_id,
                 {"severity": severity, "recipients": recipients, "overdue_hours": overdue_hours})

    util.ok({"id": task_id, "escalated": True, "severity": severity,
             "mail_ids": mail_ids, "recipients": recipients,
             "overdue_hours": overdue_hours, "deduplicated": False,
             "duplicate": False, "duplicate_suppressed": False,
             "status": status},
           elapsed_ms=int((time.time() - start) * 1000))


def cmd_task(args: Namespace) -> None:
    sub = getattr(args, "task_command", None)
    if not sub:
        util.fail(util.EXIT_USAGE_ERROR, "no_task_subcommand")
    profile = util.resolve_profile(args)
    if sub not in ("show", "list", "board"):
        from . import locks as _locks
        _locks.ensure_scope_writable("tasks", profile)
    if sub == "new":
        cmd_task_new(args, profile)
    elif sub in TRANSITIONS:
        _apply_transition(args, profile, sub)
    elif sub == "show":
        cmd_task_show(args, profile)
    elif sub == "list":
        cmd_task_list(args, profile)
    elif sub == "board":
        cmd_task_board(args, profile)
    elif sub == "edit":
        cmd_task_edit(args, profile)
    elif sub == "link":
        cmd_task_link(args, profile)
    elif sub == "escalate":
        cmd_task_escalate(args, profile)
    else:
        util.fail(util.EXIT_USAGE_ERROR, f"unknown task subcommand: {sub}")
