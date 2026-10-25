"""Mail / ticket commands."""

from __future__ import annotations

import hashlib
import json
import sys
import os
import shutil
import subprocess
import time
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import access as acl_mod
from . import auditlog, idempotency, index, journal, schema, scrubber, util


def _mail_path(to: str, id_: str, created: datetime, box: str = "inbox") -> Path:
    date = created.strftime("%Y-%m-%d")
    return util.HOME / "mail" / to / box / f"{date}_{id_}.md"


def _target_state(target: str) -> str | None:
    path = util.HOME / "status" / f"{target}.md"
    if not path.exists():
        return None
    rec = util.read_record(path)
    if not rec:
        return None
    state = rec.get("state")
    if state not in schema.VALID_STATES:
        return None
    until = rec.get("until")
    if until:
        try:
            until_dt = util.parse_utc(until)
        except Exception:
            return None
        if until_dt <= util.now_utc():
            return None
    return state


def _write_wake_marker(to: str, from_: str, mail_id: str, created_at: str, timeout: int) -> Path:
    marker_dir = util.HOME / "wake"
    marker_dir.mkdir(parents=True, exist_ok=True)
    path = marker_dir / f"{to}.wake"
    payload = {
        "from": from_,
        "mail_id": mail_id,
        "created_at": created_at,
        "expires_at": util.utc_iso(util.parse_utc(created_at) + timedelta(seconds=timeout)),
    }
    util.write_atomically(path, json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def _trigger_wake_hook(target: str, mail_id: str, from_: str) -> None:
    dispatcher = shutil.which("a2a-wake-dispatcher")
    if dispatcher is None:
        return
    try:
        subprocess.run(
            [dispatcher, target, mail_id, from_],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        pass


def _attach_meta(path_str: str) -> dict:
    path = Path(path_str).expanduser()
    st = path.stat()
    sha = None
    if path.is_file():
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        sha = h.hexdigest()
    return {
        "path": str(path),
        "sha256": sha,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "mtime_iso": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
        "name": path.name,
        "basename": path.name,
    }


def cmd_mail_send(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    from . import locks as _locks
    _locks.ensure_scope_writable("mail", profile)
    if not util.validate_profile_name(args.to):
        util.fail(util.EXIT_USAGE_ERROR, "invalid_profile_name")
    if profile == args.to:
        util.fail(util.EXIT_USAGE_ERROR, "self_send_forbidden")
    # v0.5.0 (A-01): unknown recipient is a refusal when the bus carries a
    # profiles manifest — no more letters addressed to typo'd ghosts.
    acl_mod.enforce_known(args.to, "recipient")

    access = args.access or schema.DEFAULT_ACCESS["mail"]
    depth = getattr(args, "depth", None) or 1
    _tags_raw = getattr(args, "tags", None)
    _tags = [t.strip() for t in str(_tags_raw).split(",") if t.strip()] if _tags_raw else []

    # v0.8.0 §4: вложения=ссылки (не копируем содержимое)
    _attach_raw = getattr(args, "attach", None)
    _attach_list = []
    if _attach_raw:
        for item in _attach_raw:
            if isinstance(item, list):
                _attach_list.extend(item)
            else:
                _attach_list.append(item)
    _attach_meta_list = []
    for _p in _attach_list:
        _pp = Path(str(_p)).expanduser()
        if not _pp.exists():
            util.fail_hint(util.EXIT_ERROR, "attach_failed",
                           "attachment path does not exist; provide an existing file",
                           "a2a mail send --to other --subject x --body y --attach /path/to/file",
                           {"path": str(_pp)})
        _attach_meta_list.append(_attach_meta(str(_pp)))
    _tags_list = [s.strip() for s in (getattr(args, "tags", None) or "").split(",") if s.strip()]
    from . import scrub_config as _sc
    _sc.assert_not_blocked("mail", args.subject or "", str(getattr(args, "body", "") or ""))
    meta = {
        "type": "mail",
        "author": profile,
        "to": args.to,
        "access": access,
        "tags": _tags_list or None,
        "subject": args.subject,
        "msg_status": "unread",
        "priority": args.priority or "fyi",
        "reply_to": args.ref or None,
        "depth": depth,
        "tags": _tags,
        "attachments": _attach_meta_list,
        "files": _attach_meta_list,
    }
    wake_requested = bool(getattr(args, "wake", False))
    # v0.4.5: дисциплина ответов — today-почта будит адресата по умолчанию
    # (письма без wake неделями лежали непрочитанными: спящий профиль не знает,
    # что его спрашивали). fyi остаётся тихим. --no-wake глушит осознанно.
    if getattr(args, "wake", None) is None:
        wake_requested = (args.priority or "fyi") == "today"
    if wake_requested and depth < 2:
        meta["wake"] = True
    schema.apply_defaults(meta, "mail")

    # v0.8.0 §1: thread = id корня цепочки; при --ref наследуется от исходного.
    _thread_id = meta["id"]
    if getattr(args, "ref", None):
        _orig, _ = _find_mail_global(args.ref)
        if _orig:
            _thread_id = _orig.get("thread") or _orig.get("id") or args.ref
        else:
            _thread_id = args.ref
    meta["thread"] = _thread_id

    ok, reason = schema.validate(meta, args.body or "")
    if not ok:
        util.fail(util.EXIT_INVALID_RECORD, reason)

    body = args.body or ""

    # --- v0.6.0 §7 (G3-pisma): poka-yoke на числа — семантика ЖУРНАЛЬНАЯ:
    # claims без (verify И verify_run) и без --numbers-ok = отказ. Только для
    # priority today (действенные письма); fyi не затронут. Унаследованные из
    # оригинала клеймы (reply на "Re: ... 2007 тестов") не душат канал ответа.
    verify = getattr(args, "verify", None)
    verify_run = bool(getattr(args, "verify_run", False))
    numbers_ok = bool(getattr(args, "numbers_ok", False))
    verify_exit_code = None
    verify_output = None
    verify_status = None
    if meta["priority"] == "today":
        claims = journal.number_claims(args.subject, body)
        if claims and getattr(args, "ref", None):
            orig, _ = _find_mail(profile, args.ref)
            if orig:
                inherited = set(journal.number_claims(str(orig.get("subject") or ""),
                                                       orig.get("_body", "") or ""))
                claims = [c for c in claims if c not in inherited]
        if claims and not numbers_ok and not (verify and verify_run):
            util.fail_hint(util.EXIT_ERROR, "numbers_without_verify",
                          "прогони --verify CMD --verify-run или сознательно --numbers-ok (как в журнале)",
                          'a2a mail send --to other --subject x --body y --verify "cmd" --verify-run',
                          {"hits": claims, "channel": "mail_send"})
        if verify and verify_run:
            import subprocess
            try:
                proc = subprocess.run(verify, shell=True, capture_output=True,
                                      text=True, timeout=30, check=False)
                verify_exit_code = proc.returncode
                verify_output = scrubber.scrub_echo((proc.stdout + proc.stderr).strip())
            except subprocess.TimeoutExpired:
                verify_exit_code = -1
                verify_output = "verify_timeout"
            verify_status = "verified" if verify_exit_code == 0 else "VERIFY_FAILED"

    if verify:
        meta["verify"] = verify
    if verify_exit_code is not None:
        meta["verify_exit_code"] = verify_exit_code
        meta["verify_output"] = scrubber.scrub_value(verify_output)
    if verify_status == "VERIFY_FAILED":
        meta["verify_status"] = "VERIFY_FAILED"

    bundle_id = util.new_id()
    meta["bundle_id"] = bundle_id

    text = util.join_front_matter(meta, body)
    # v0.6.0 §1: новые kv/yaml-формы редактируются (доставка не ломается),
    # legacy-токены и UNREDACTABLE остаются hard-reject (контракт v0.4.10).
    _orig_text = text
    hits = scrubber.scan(text)
    if hits:
        hs = set(hits)
        if hs & scrubber.UNREDACTABLE or not hs <= scrubber.NEW_PATTERNS:
            try:
                from . import quarantine as _q
                _q.quarantine_payload(profile, _orig_text, hits)
            except Exception:
                pass
            util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", "a2a mail send --to other --subject x --body y", {"hits": hits})
        meta = scrubber.scrub_value(meta)
        body = scrubber.scrub_value(body)
        text = util.join_front_matter(meta, body)
        residual = scrubber.scan(text)
        if residual:
            try:
                from . import quarantine as _q
                _q.quarantine_payload(profile, _orig_text, residual)
            except Exception:
                pass
            util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", "a2a mail send --to other --subject x --body y", {"hits": sorted(set(residual))})
        auditlog.log("secret_redacted", profile, "mail_send",
                     {"hits": sorted(hs), "id": meta["id"]})

    created = util.parse_utc(meta["created_at"])
    out_path = util.HOME / "mail" / profile / "outbox" / f"{created.strftime('%Y-%m-%d')}_{meta['id']}.md"
    in_path = util.HOME / "mail" / args.to / "inbox" / f"{created.strftime('%Y-%m-%d')}_{meta['id']}.md"

    # v0.5.0 (G-01): idempotency window — повторный send с тем же ключом
    # возвращает исходный id без второй доставки (duplicate_suppressed).
    idem_key = getattr(args, "idempotency_key", None)
    if idem_key:
        prior = idempotency.lookup("mail_send", idem_key)
        if prior:
            util.ok(
                {"id": prior, "to": args.to, "duplicate": True,
                 "duplicate_window": True, "duplicate_suppressed": True,
                 "reason": "duplicate_window",
                 "outbox": str(util.HOME / "mail" / profile / "outbox"),
                 "inbox": str(util.HOME / "mail" / args.to / "inbox")},
                extra_metadata={"duplicate": True, "duplicate_window": True,
                                "duplicate_suppressed": True, "reason": "duplicate_window"},
                elapsed_ms=int((time.time() - start) * 1000),
            )
        idempotency.reserve("mail_send", idem_key, meta["id"])

    # v0.5.0 (G-02/J-07): outbox+inbox lands as one bundle transaction —
    # a crash mid-delivery is healed idempotently by `a2a doctor`.
    try:
        util.commit_bundle([(out_path, text), (in_path, text)], label=f"mail:{meta['id']}", bundle_id=bundle_id)
    except OSError as exc:
        util.fail(util.EXIT_ERROR, "bundle_commit_failed",
                  {"error": str(exc), "id": meta["id"], "hint": "a2a doctor recovers"})
    if idem_key:
        idempotency.confirm("mail_send", idem_key, meta["id"])

    data: dict = {"id": meta["id"], "to": args.to, "priority": meta["priority"], "outbox": str(out_path), "inbox": str(in_path)}
    if _attach_meta_list:
        data["attachments"] = _attach_meta_list
        data["files"] = _attach_meta_list
    if verify_status:
        data["verify_status"] = verify_status
        data["verify_exit_code"] = verify_exit_code
    if wake_requested:
        state = _target_state(args.to)
        dnd_warning = None
        if state == "dnd":
            try:
                from . import dnd_policy as _dp
                _policy, dnd_warning, _missing = _dp.read_policy()
                allowed = _dp.matches("mail", priority=meta.get("priority"))
            except Exception:
                allowed = False
            if allowed:
                state = None
        if state in ("dnd", "busy"):
            data["wake"] = {"triggered": False, "target": args.to, "suppressed": f"target_{state}"}
            if dnd_warning:
                data["dnd_policy_warning"] = dnd_warning
        elif depth >= 2:
            data["wake"] = {"triggered": False, "target": args.to, "suppressed": "max_depth"}
        else:
            timeout = getattr(args, "timeout", None) or 60
            _write_wake_marker(args.to, profile, meta["id"], meta["created_at"], timeout)
            _trigger_wake_hook(args.to, meta["id"], profile)
            data["wake"] = {"triggered": True, "target": args.to}

    util.ok(data, elapsed_ms=int((time.time() - start) * 1000))


def cmd_mail_list(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    entries = []
    # Default (no flags) = inbox. --inbox forces inbox; --outbox switches to
    # outbox (or adds it when combined with --inbox). --unread filters either.
    include_inbox = args.inbox or not args.outbox
    include_outbox = args.outbox
    if include_inbox:
        entries.extend(_collect_box(profile, "inbox", args.unread))
    if include_outbox:
        entries.extend(_collect_box(profile, "outbox", args.unread))
    util.ok({"count": len(entries), "entries": entries}, elapsed_ms=int((time.time() - start) * 1000))


def cmd_mail_check(args: Namespace) -> None:
    """Unread inbox check for hooks; listing never changes the read state."""
    start = time.time()
    profile = util.resolve_profile(args)
    entries = _collect_box(profile, "inbox", only_unread=True)
    if getattr(args, "include_body", False):
        for entry in entries:
            rec, _ = _find_mail(profile, entry["id"])
            if rec is None:
                # File was removed by a concurrent process (e.g., mail prune).
                continue
            entry["body"] = rec.get("_body", "")
    # v0.5.0 (DESIGN §1): wake-check получает секцию задач «просрочено/сегодня»
    # без отдельного крона. Read-only: ни одной записи от mail check.
    data: dict = {"count": len(entries), "entries": entries}
    try:
        from . import tasks as _tasks
        data["tasks"] = _tasks.overdue_summary(profile)
    except Exception as exc:  # pragma: no cover - degraded, not fatal
        data["tasks"] = {"overdue": 0, "due_today": 0, "error": f"task_scan_failed: {exc}"}
    util.ok(data, elapsed_ms=int((time.time() - start) * 1000))


def _collect_box(profile: str, box: str, only_unread: bool) -> list[dict]:
    entries = []
    base = util.HOME / "mail" / profile / box
    for path in util.list_files(base):
        rec = util.read_record(path)
        if not rec:
            continue
        # v0.5.0 (J-05): box listing is path-scoped; only an explicit `acl`
        # narrows it. Records without acl keep v0.4.10 semantics exactly.
        if not acl_mod.acl_only(rec, profile):
            continue
        if only_unread and rec.get("msg_status") != "unread":
            continue
        entries.append({
            "id": rec["id"],
            "author": rec.get("author"),
            "to": rec.get("to"),
            "subject": rec.get("subject"),
            "msg_status": rec.get("msg_status"),
            "priority": rec.get("priority"),
            "created_at": rec.get("created_at"),
        })
    return entries


def cmd_mail_read(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    rec, path = _find_mail(profile, args.id)
    if not rec:
        util.fail(util.EXIT_USAGE_ERROR, "mail_not_found")
    if rec.get("msg_status") == "unread":
        rec["msg_status"] = "read"
        text = util.join_front_matter(rec, rec.get("_body", ""))
        util.write_atomically(path, text)
    # v0.8.0 §5: честный факт прочтения (read = наблюдатель, ничего не принуждаем)
    try:
        rr_path = util.HOME / ".state" / "read_receipts.log"
        util.ensure_dir(rr_path)
        entry = {"mail_id": rec.get("id"), "reader": profile, "ts": util.utc_iso()}
        with open(rr_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass
    util.ok({"id": rec["id"], "msg_status": rec["msg_status"], "subject": rec.get("subject"), "body": rec.get("_body")}, elapsed_ms=int((time.time() - start) * 1000))


def cmd_mail_receipt(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    rec, _path = _find_mail(profile, args.id)
    if not rec:
        rec, _path = _find_mail_global(args.id)
    if not rec:
        util.fail(util.EXIT_USAGE_ERROR, "mail_not_found")
    if str(rec.get("author") or "") != profile and str(rec.get("owner") or "") != profile:
        util.fail(util.EXIT_USAGE_ERROR, "not_allowed")
    mail_id = rec.get("id")
    rr_path = util.HOME / ".state" / "read_receipts.log"
    events: list[dict] = []
    if rr_path.exists():
        for line in rr_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("mail_id") == mail_id:
                events.append(ev)
    events.sort(key=lambda e: str(e.get("ts") or ""))
    readers: list[str] = []
    seen: set[str] = set()
    for ev in events:
        r = ev.get("reader")
        if r is None:
            continue
        rs = str(r)
        if rs not in seen:
            seen.add(rs)
            readers.append(rs)
    bundle_id = rec.get("bundle_id") if isinstance(rec, dict) else None
    data = {
        "id": mail_id,
        "mail_id": mail_id,
        "count": len(events),
        "readers_count": len(readers),
        "readers": readers,
        "entries": events,
        "receipts": events,
        "bundle_id": bundle_id,
        "bundle-id": bundle_id,
        "bundleId": bundle_id,
    }
    util.ok(data, elapsed_ms=int((time.time() - start) * 1000))


def cmd_mail_mark_read(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    rec, path = _find_mail(profile, args.id)
    if not rec:
        util.fail(util.EXIT_USAGE_ERROR, "mail_not_found")
    if rec.get("msg_status") == "unread":
        rec["msg_status"] = "read"
        text = util.join_front_matter(rec, rec.get("_body", ""))
        util.write_atomically(path, text)
    util.ok({"id": rec["id"], "msg_status": rec["msg_status"], "subject": rec.get("subject")}, elapsed_ms=int((time.time() - start) * 1000))


def cmd_mail_answer(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    rec, path = _find_mail(profile, args.id)
    if not rec:
        util.fail(util.EXIT_USAGE_ERROR, "mail_not_found")

    idem_key = getattr(args, "idempotency_key", None)
    if idem_key:
        from . import idempotency
        prior = idempotency.lookup("mail_answer", idem_key)
        if prior:
            util.ok(
                {"id": rec["id"], "answered": True, "reply_id": prior,
                 "duplicate": True, "duplicate_suppressed": True,
                 "duplicate_reason": "duplicate_window"},
                elapsed_ms=int((time.time() - start) * 1000),
            )
            return
    # v0.4.9 (sa-0, high): валидация текста ответа ДО пометки — при отказе
    # письмо остаётся unread. Полный порядок (статус после доставки) — v0.5.
    # v0.6.0 §1: новые kv/yaml-формы в ответе редактируются, legacy — reject.
    clean_text, hits, action = scrubber.hard_channel_scrub(args.text or "")
    if action == "reject":
        try:
            from . import quarantine as _q
            _q.quarantine_payload(profile, args.text or "", hits)
        except Exception:
            pass
        util.fail_hint(util.EXIT_USAGE_ERROR, "secret_detected_in_reply",
                      "secret material detected in reply; remove or redact before sending",
                      "a2a mail answer <id> \"text without secrets\"",
                      {"categories": hits})
    if action == "redacted":
        auditlog.log("secret_redacted", profile, "mail_answer",
                     {"hits": sorted(set(hits)), "id": rec["id"]})
        args.text = clean_text
    # v1.0.0 fix: пометка answered — ТОЛЬКО после успешной доставки ответа
    # (контракт v0.4.9: при отказе письмо остаётся unread).
    send_args = Namespace(
        to=rec["author"],
        subject=f"Re: {rec.get('subject', '')}",
        body=args.text,
        priority="today",
        ref=rec["id"],
        access=None,
    )
    # v0.6.0 §7: те же обходы poka-yoke, что и у journal/mail send
    send_args.numbers_ok = bool(getattr(args, "numbers_ok", False))
    send_args.verify = getattr(args, "verify", None)
    send_args.verify_run = bool(getattr(args, "verify_run", False))
    send_args.idempotency_key = None
    for attr in ("profile", "as_"):
        setattr(send_args, attr, getattr(args, attr, None))

    import contextlib
    import io
    import json as _json
    buf = io.StringIO()
    code_exit = 0
    with contextlib.redirect_stdout(buf):
        try:
            cmd_mail_send(send_args)
        except SystemExit as se:
            code_exit = int(se.code or 0)
    envelope = buf.getvalue()
    reply_obj = {}
    try:
        reply_obj = _json.loads(envelope.strip() or "{}")
    except Exception:
        reply_obj = {}
    if code_exit != 0 or not reply_obj.get("ok", False):
        # отказ/ошибка доставки: original НЕ помечаем, конверт пробрасываем как свой
        sys.stdout.write(envelope)
        sys.exit(code_exit or 1)
    reply_id = (reply_obj.get("data") or {}).get("id")
    rec["msg_status"] = "answered"
    text = util.join_front_matter(rec, rec.get("_body", ""))
    util.write_atomically(path, text)
    if idem_key and reply_id:
        from . import idempotency
        idempotency.reserve("mail_answer", idem_key, reply_id)
        idempotency.confirm("mail_answer", idem_key, reply_id)
    util.ok({"id": rec["id"], "answered": True, "reply_id": reply_id,
             "subject": rec.get("subject")},
            elapsed_ms=int((time.time() - start) * 1000))


def cmd_mail_archive(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    cutoff = util.now_utc() - timedelta(days=30)
    moved = 0
    for box in ("inbox", "outbox"):
        for path in util.list_files(util.HOME / "mail" / profile / box):
            rec = util.read_record(path)
            if not rec:
                continue
            try:
                created = util.parse_utc(rec["created_at"])
            except Exception:
                continue
            if created < cutoff:
                # v0.4.10 (review R2): архив писался в mail/archive/<profile>,
                # индекс читал mail/<profile>/archive — письма выпадали из поиска навсегда.
                archive_dir = util.HOME / "mail" / profile / "archive"
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = archive_dir / path.name
                shutil.move(str(path), str(dest))
                moved += 1
    util.ok({"archived": moved}, elapsed_ms=int((time.time() - start) * 1000))


def cmd_mail_prune(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    cutoff = util.now_utc() - timedelta(days=args.days)
    removed = 0
    for box in ("inbox", "outbox"):
        for path in util.list_files(util.HOME / "mail" / profile / box):
            rec = util.read_record(path)
            if not rec:
                continue
            status = rec.get("msg_status")
            if status not in ("read", "answered", "resolved", "archived"):
                continue
            try:
                created = util.parse_utc(rec["created_at"])
            except Exception:
                continue
            if created < cutoff:
                if not args.dry_run:
                    path.unlink()
                removed += 1
    if not args.dry_run:
        index.rebuild()
    util.ok({"pruned": removed, "dry_run": args.dry_run, "days": args.days}, elapsed_ms=int((time.time() - start) * 1000))


def cmd_mail_attachment(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    rec, _path = _find_mail_global(args.id)
    if not rec:
        util.fail(util.EXIT_USAGE_ERROR, "mail_not_found")
    from . import access as _acl
    if not _acl.may_read(rec, profile):
        util.fail(util.EXIT_USAGE_ERROR, "not_allowed")
    atts = rec.get("attachments")
    if not isinstance(atts, list):
        atts = rec.get("files")
    if not isinstance(atts, list) or not atts:
        util.fail(util.EXIT_USAGE_ERROR, "attachment_not_found")
    idx = int(getattr(args, "n", 1)) - 1
    if idx < 0 or idx >= len(atts):
        util.fail(util.EXIT_USAGE_ERROR, "attachment_not_found")
    att = dict(atts[idx] or {})
    p = Path(str(att.get("path", ""))).expanduser()
    exists = p.exists()
    current_sha = None
    current_size = None
    current_mtime = None
    sha_match = False
    if exists:
        st = p.stat()
        current_size = st.st_size
        current_mtime = st.st_mtime
        if p.is_file():
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            current_sha = h.hexdigest()
        if p.is_dir():
            sha_match = (att.get("sha256") is None)
        else:
            sha_match = (current_sha == att.get("sha256"))
    if not exists:
        status = "MISSING"
    elif sha_match:
        status = "ok"
    else:
        status = "MOVED"
    out_att = dict(att)
    out_att.update({
        "mail_id": rec.get("id"),
        "id": rec.get("id"),
        "n": int(getattr(args, "n", 1)),
        "index": int(getattr(args, "n", 1)),
        "path": att.get("path"),
        "name": att.get("name") or p.name,
        "basename": att.get("basename") or p.name,
        "filename": att.get("filename") or p.name,
        "exists": exists,
        "sha_match": sha_match,
        "current_sha256": current_sha,
        "current_size": current_size,
        "current_mtime": current_mtime,
        "status": status,
        "verify_status": "verified" if status == "ok" else status,
        "verify": "verified" if status == "ok" else status,
        "result": status,
    })
    util.ok(out_att, elapsed_ms=int((time.time() - start) * 1000))


def _find_mail(profile: str, id_: str):
    for box in ("inbox", "outbox"):
        for path in util.list_files(util.HOME / "mail" / profile / box):
            rec = util.read_record(path)
            if rec and rec.get("id") == id_:
                return rec, path
    return None, None


def _find_mail_global(id_: str):
    root = util.HOME / "mail"
    for path in util.list_files(root):
        rec = util.read_record(path)
        if rec and rec.get("id") == id_:
            return rec, path
    return None, None


def _thread_readable(rec: dict, profile: str) -> bool:
    if not acl_mod.may_read(rec, profile):
        return False
    acl = rec.get("acl")
    if isinstance(acl, list) and acl:
        return True
    return profile in {str(rec.get("author") or ""), str(rec.get("to") or ""), str(rec.get("owner") or "")}


def cmd_mail_thread(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    readable = []
    for path in util.list_files(util.HOME / "mail"):
        rec = util.read_record(path)
        if not rec or rec.get("type") != "mail":
            continue
        if not _thread_readable(rec, profile):
            continue
        readable.append(rec)

    seeds = [r for r in readable if r.get("id") == args.id]
    if not seeds:
        util.fail(util.EXIT_USAGE_ERROR, "mail_not_found")
    root = seeds[0].get("thread") or seeds[0].get("id") or args.id

    seen = set()
    chain = []
    for r in readable:
        if (r.get("thread") or r.get("id")) != root:
            continue
        if r.get("id") in seen:
            continue
        seen.add(r.get("id"))
        chain.append(r)
    chain.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("id") or "")))

    entries = [
        {
            "id": r.get("id"),
            "created_at": r.get("created_at"),
            "author": r.get("author"),
            "subject": r.get("subject"),
        }
        for r in chain
    ]
    util.ok(
        {"id": root, "thread": root, "count": len(entries), "entries": entries},
        elapsed_ms=int((time.time() - start) * 1000),
    )
