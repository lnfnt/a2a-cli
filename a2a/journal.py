"""Journal / handoff-shift log commands."""

from __future__ import annotations

import time
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import auditlog
import hashlib
import os
import re
import shlex
import subprocess

from . import schema, scrubber, util
from . import access as acl_mod

# v0.6.0 §8: лимиты хэш-дерева каталога-артефакта
TREE_MAX_FILES = 500
TREE_MAX_BYTES = 100 * 1024 * 1024


def _dir_tree(root: Path) -> dict:
    """v0.6.0 §8 (B-02): полное хэш-дерево каталога-артефакта.

    Рекурсивно, детерминированный порядок (sorted relpath). Лимиты:
    500 файлов / 100MB суммарно; при превышении — честный status
    "tree_overflow" и частичный (не пустой!) список уже просчитанных файлов.
    """
    files: list[dict] = []
    total = 0
    status = "ok"
    for p in sorted(root.rglob("*")):
        try:
            if not p.is_file():
                continue
            size = p.stat().st_size
        except OSError:
            continue
        if len(files) >= TREE_MAX_FILES or total + size > TREE_MAX_BYTES:
            status = "tree_overflow"
            break
        h = hashlib.sha256()
        try:
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        except Exception as exc:
            files.append({"path": str(p.relative_to(root)), "sha256": None,
                          "error": str(exc)})
            continue
        files.append({"path": str(p.relative_to(root)), "sha256": h.hexdigest(),
                      "bytes": size})
        total += size
    agg = hashlib.sha256(
        "\n".join(f"{f['path']}:{f.get('sha256')}" for f in files).encode("utf-8")
    ).hexdigest()
    return {"status": status, "truncated": status == "tree_overflow",
            "files": files, "file_count": len(files), "total_bytes": total,
            "tree_sha256": agg}


def _artifact_proofs(artifacts: list[str]) -> list[dict]:
    """v0.2.1 (review pass 3): зафиксировать физическое доказательство артефакта.

    Для каждого пути: exists, mtime (UTC ISO), sha256 (для файлов <= 64МБ).
    Несуществующий путь остаётся в списке с exists=False — факт попытки
    ссылки виден в записи.
    """
    proofs = []
    for raw in artifacts:
        path = Path(raw).expanduser()
        entry: dict = {"path": str(path), "exists": path.exists()}
        if entry["exists"]:
            try:
                stat = path.stat()
                entry["mtime"] = datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(timespec="seconds")
                # v0.4.8: каталог — легитимный путь артефакта (репозиторий,
                # релизное дерево). sha256 от каталога не берём: open() на нём
                # бросал IsADirectoryError, handler перелистовал exists=False
                # и ложно клеймил живую директорию как UNVERIFIED_CLAIM.
                if path.is_dir():
                    entry["sha256"] = None
                    entry["sha256_note"] = "directory"
                    # v0.6.0 §8: каталог — полное хэш-дерево (с лимитами)
                    entry["tree"] = _dir_tree(path)
                elif stat.st_size <= 64 * 1024 * 1024:
                    h = hashlib.sha256()
                    with open(path, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            h.update(chunk)
                    entry["sha256"] = h.hexdigest()
                else:
                    entry["sha256"] = None
                    entry["sha256_note"] = "file too large"
            except Exception as exc:
                # exists остаётся фактом пути; сбой хэша/стата пишем в error,
                # но не превращаем существующий артефакт в несуществующий.
                entry["error"] = str(exc)
        proofs.append(entry)
    return proofs


def _next_journal_seq() -> int:
    """Монотонный seq журнала (v0.4.1, P0-6/P1-8).

    Единый счётчик в .state/journal_seq под flock: параллельные писатели
    получают строгую последовательность без дыр и дублей. Счётчик survives
    обрывов (fsync). База при первом запуске — число существующих записей.
    """
    import fcntl

    state_dir = util.HOME / ".state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state = state_dir / "journal_seq"
    lock = state_dir / "journal_seq.lock"
    with open(lock, "a+") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            current = 0
            if state.exists():
                try:
                    current = int(state.read_text(encoding="utf-8").strip() or "0")
                except ValueError:
                    current = 0
            if current <= 0:
                base = len(util.list_files(util.HOME / "journal"))
                current = max(base, 0)
            nxt = current + 1
            tmp = state.with_suffix(state.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(str(nxt))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, state)
            return nxt
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


_NUM_UNITS = (
    r"автотест|тест|запис|файл|репозитор|релиз|профил|строк|письм|коммит|"
    r"замет|wikilink|скилл|сесси|копи|модул|индекс|баг|дефект|кейс|case|issue|md"
)
_NUM_CLAIM_RE = re.compile(
    rf"(?<![\w.:/-])\d{{1,7}}(?:[ ,.]*\d)*\s*(?:{_NUM_UNITS})[a-zа-яё]*"
    rf"|(?<![\w.:/-])\d{{1,7}}\s*%"
    r"|(?<![\w.,:/-])(?!19\d{2}|20\d{2})\d{3,}(?![\w.,:%/-])",
    re.IGNORECASE,
)


def number_claims(*texts: str) -> list:
    """Числовые утверждения-клеймы: >=3 знаков или число при счётном слове.
    Даты (19xx/20xx), версии (d.d), пути, hex-id, HH:MM не считаются."""
    hits: list = []
    for t in texts:
        for m in _NUM_CLAIM_RE.finditer(t or ""):
            s = m.group(0).strip()
            if s and s not in hits:
                hits.append(s)
    return hits[:5]


def cmd_log(args: Namespace) -> None:
    start = time.time()
    response = write_entry(args)
    util.ok(response, elapsed_ms=int((time.time() - start) * 1000))


def write_entry(args: Namespace, defer: bool = False) -> dict:
    _tags = [s.strip() for s in (getattr(args, "tags", None) or "").split(",") if s.strip()]
    """Build, validate and write one journal record; returns the response.

    v0.5.0: extracted from cmd_log so task transitions can auto-write a
    linked journal entry through the same validation pipeline. Behaviour is
    identical to v0.4.10 `log`; util.fail on errors still raises SystemExit.
    Optional attr `task_id` links the entry to a task (bidirectional link).
    `defer=True` validates and computes path/text but does NOT write — the
    caller commits the record through util.commit_bundle (response then
    carries "text").
    """
    profile = util.resolve_profile(args)
    from . import locks as _locks
    _locks.ensure_scope_writable("journal", profile)
    if getattr(args, "private", False):
        access = "L1"
    else:
        access = args.access or schema.DEFAULT_ACCESS["journal"]
    if access not in schema.ACCESS_LEVELS:
        util.fail(util.EXIT_INVALID_RECORD, "invalid_access")

    # v0.4.7 poka-yoke (норма lnfnt): числовое утверждение без проганной
    # verify-команды в журнал не пишется; обход — только явным --numbers-ok.
    claims = number_claims(args.what_done, args.left or "")
    if claims and not getattr(args, "numbers_ok", False) \
            and not (args.verify and getattr(args, "verify_run", False)):
        util.fail_hint(util.EXIT_ERROR, "numbers_without_verify",
                      "числовые утверждения требуют --verify CMD --verify-run или явный --numbers-ok",
                      'a2a log "entry" --verify "cmd" --verify-run',
                      {"hits": claims})

    artifacts = []
    if args.artifacts:
        artifacts = [p.strip() for p in args.artifacts.split(",") if p.strip()]

    deadline = None
    if args.deadline:
        try:
            deadline = datetime.strptime(args.deadline, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            util.fail(util.EXIT_INVALID_RECORD, "invalid_deadline")

    status = schema.journal_status({"artifacts": artifacts, "status": args.status})

    # v0.2.1: физические доказательства артефактов (mtime + sha256)
    artifact_proofs = _artifact_proofs(artifacts) if artifacts else []
    if artifacts and status == "ok":
        # все артефакты должны существовать: иначе UNVERIFIED_CLAIM
        if not all(a.get("exists") for a in artifact_proofs):
            status = "UNVERIFIED_CLAIM"

    verify_exit_code = None
    verify_output = None
    if args.verify and getattr(args, "verify_run", False):
        try:
            proc = subprocess.run(
                args.verify,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            verify_exit_code = -1
            verify_output = "verify_timeout"
        else:
            verify_exit_code = proc.returncode
            # P2-5 finding#1: verify-ывод идёт и в файл (meta), и в stdout —
            # никогда не эхомим credentials: sanitize на точке захвата.
            verify_output = scrubber.scrub_echo((proc.stdout + proc.stderr).strip())
        if verify_exit_code != 0:
            status = "VERIFY_FAILED"
        elif status == "ok":
            status = "verified"

        from . import scrub_config as _sc
        _sc.assert_not_blocked("journal", args.what_done or "", args.left or "")
    meta = {
        "type": "journal",
        "author": profile,
        "access": access,
        "private": bool(getattr(args, "private", False)),
        "tags": _tags or None,
        "seq": _next_journal_seq(),
        "session": getattr(args, "session", None) or None,
        "what_done": args.what_done,
        "what_left": args.left or None,
        "deadline": util.utc_iso(deadline) if deadline else None,
        "artifacts": artifacts,
        "artifact_proofs": artifact_proofs,
        "verify": args.verify or None,
        "status": status,
    }
    # v0.5.0: task↔journal bidirectional link; only present when set so
    # existing journal records keep their exact field set (back-compat).
    task_link = getattr(args, "task_id", None)
    if task_link:
        meta["task_id"] = task_link
    if verify_exit_code is not None:
        meta["verify_exit_code"] = verify_exit_code
        meta["verify_output"] = verify_output
    schema.apply_defaults(meta, "journal")

    ok, reason = schema.validate(meta, "")
    if not ok:
        util.fail(util.EXIT_INVALID_RECORD, reason)

    body = f"{args.what_done}\n"
    if args.left:
        body += f"\nLeft: {args.left}\n"

    text = util.join_front_matter(meta, body)
    dirty, hits = scrubber.check(text)
    _orig_text = text
    if dirty:
        mode = os.environ.get("A2A_SCRUB_MODE", "redact").strip().lower()
        if mode == "redact" and not (set(hits) & scrubber.UNREDACTABLE):
            meta["scrubbed"] = ",".join(sorted(set(hits)))
            # v0.6.0 §1: пофилдовый redact ДО сериализации (yaml-кавычки целы)
            meta = scrubber.scrub_value(meta)
            body = scrubber.scrub_value(body)
            text = util.join_front_matter(meta, body)
            residual = scrubber.scan(text)  # defense: что-то не вырезалось — не пишем
            if residual:
                try:
                    from . import quarantine as _q
                    _q.quarantine_payload(profile, _orig_text, residual)
                except Exception:
                    pass
                util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", 'a2a log "entry without secrets"', {"hits": sorted(set(residual))})
            # v0.6.0 §1: redact-событие в аудит на всех каналах
            auditlog.log("secret_redacted", profile, "journal",
                         {"hits": sorted(set(hits)), "id": meta["id"]})
        else:
            try:
                from . import quarantine as _q
                _q.quarantine_payload(profile, _orig_text, hits)
            except Exception:
                pass
            util.fail_hint(util.EXIT_ERROR, "secret_detected", "secret material detected; remove or redact before sending", 'a2a log "entry without secrets"', {"hits": hits})

    created = util.parse_utc(meta["created_at"])
    path = util.record_path("journal", profile, access, created, meta["id"])
    if not defer:
        util.write_atomically(path, text)
    response = {"id": meta["id"], "path": str(path), "status": status, "unverified": status == "UNVERIFIED_CLAIM", "seq": meta.get("seq")}
    if meta.get("private"):
        response["private"] = True
    if defer:
        response["text"] = text
    if meta.get("scrubbed"):
        response["scrubbed"] = meta["scrubbed"]
    if meta.get("session"):
        response["session"] = meta["session"]
    if verify_exit_code is not None:
        response["verify_exit_code"] = verify_exit_code
        response["verify_output"] = verify_output
    if meta.get("task_id"):
        response["task_id"] = meta["task_id"]
    return response


def cmd_journal(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    today = util.now_utc().date()
    entries = []
    for path in util.list_files(util.HOME / "journal"):
        rec = util.read_record(path)
        if not rec:
            continue
        access = rec.get("access")
        if access not in schema.ACCESS_LEVELS:
            continue
        if args.today:
            created = util.parse_utc(rec["created_at"]).date()
            if created != today:
                continue
        if args.author and rec.get("author") != args.author:
            continue
        if getattr(args, "mine", False) and rec.get("author") != profile:
            continue
        if getattr(args, "private", False) and rec.get("access") != "L1":
            continue
        session_filter = getattr(args, "session", None)
        if session_filter and rec.get("session") != session_filter:
            continue
        min_seq = getattr(args, "min_seq", None)
        if min_seq is not None:
            try:
                if int(rec.get("seq") or 0) < int(min_seq):
                    continue
            except (TypeError, ValueError):
                continue  # записи без seq (до v0.4.1) не проходят min-seq фильтр
        # v0.4.10 (review R3): --all больше не обходит L1-фильтр (политика: L1 только
        # для автора). Чужой L1 — явно через --include-l1, и с audit-событием, как _cmd_read.
        # v0.5.0 (J-05): тот же контракт проходит через access.may_read; записи
        # без acl-поля ведут себя идентично (no narrowing), явный acl сужает.
        if not acl_mod.may_read(rec, profile, include_l1=getattr(args, "include_l1", False)):
            continue
        if access == "L1" and rec.get("author") != profile and getattr(args, "include_l1", False):
            acl = rec.get("acl")
            if not (isinstance(acl, list) and acl):
                auditlog.log("journal_l1_read", profile, str(rec.get("author")), {"id": rec.get("id")})
        entries.append({
            "id": rec["id"],
            "seq": rec.get("seq"),
            "session": rec.get("session"),
            "author": rec.get("author"),
            "access": rec.get("access"),
            "private": bool(rec.get("private")) or rec.get("access") == "L1",
            "what_done": rec.get("what_done"),
            "status": rec.get("status"),
            "created_at": rec["created_at"],
        })
    entries.sort(key=lambda e: (e.get("created_at") or "", e.get("seq") or 0))
    util.ok({"count": len(entries), "entries": entries}, elapsed_ms=int((time.time() - start) * 1000))
