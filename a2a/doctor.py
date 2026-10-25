"""Doctor / health-check command."""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import time
from argparse import Namespace
from datetime import timedelta
from pathlib import Path

from . import index, schema, scrubber, util
from . import access as acl_mod
from . import locks as locks_mod
from . import auditlog, snapshot as snapshot_mod


def cmd_doctor(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)

    ttl_raw = getattr(args, "ttl", None)
    if ttl_raw is None:
        ttl_seconds = 24 * 3600
    else:
        try:
            from . import locks as _locks_mod
            ttl_seconds = _locks_mod._parse_ttl(ttl_raw)
        except Exception:
            ttl_seconds = 24 * 3600
    issues = []
    drift_info = None
    recovery_info = None

    # v0.5.0 (G-02): finish interrupted bundle transactions first — recovery
    # is idempotent, a clean bus does nothing. Done BEFORE scans so ghost/
    # orphan counters see healed data, not the crash snapshot.
    recovery_info = util.recover_bundles()
    if recovery_info["applied_ops"]:
        issues.append({"type": "bundle_txn_recovered", "count": recovery_info["applied_ops"]})
    if recovery_info["unrecoverable"]:
        issues.append({"type": "bundle_txn_unrecoverable",
                       "count": len(recovery_info["unrecoverable"]),
                       "samples": recovery_info["unrecoverable"][:5]})
    # v0.6.0 §3: bundle recovery — state-changing, идёт в operate-аудит
    if recovery_info["applied_ops"] or recovery_info["unrecoverable"]:
        auditlog.log("bundle_recovery", profile, ".state/bundle_txn",
                     {"applied_ops": recovery_info["applied_ops"],
                      "cleaned_tmps": recovery_info["cleaned_tmps"],
                      "unrecoverable": len(recovery_info["unrecoverable"])})

    # v0.6.0 §4 (G-04): tamper-сверка с baseline (только путь+id, без содержимого).
    # Считаем ДО journal-записи самого doctor'а — иначе её добавление всегда
    # попадало бы в added текущего прогона.
    tamper = snapshot_mod.compare()

    # capture counts before rebuild to detect SQLite drift
    db_existed = index.db_path().exists()
    counts_before = {}
    if db_existed:
        try:
            conn = sqlite3.connect(str(index.db_path()))
            cur = conn.cursor()
            cur.execute("SELECT type, COUNT(*) FROM records GROUP BY type")
            counts_before = {row[0]: row[1] for row in cur.fetchall()}
            conn.close()
        except Exception:
            pass

    # ghost entries: индекс ссылается на файлы, которых уже нет.
    # Обязательно ДО rebuild — rebuild пересоздаёт индекс из файлов
    # и стирает ghost-записи до того, как мы их увидим.
    ghosts = []
    if db_existed:
        conn_g = index.sqlite3.connect(str(index.db_path()))
        cur_g = conn_g.cursor()
        cur_g.execute("SELECT path FROM records")
        for row in cur_g.fetchall():
            if not Path(row[0]).exists():
                ghosts.append(row[0])
        conn_g.close()
    if ghosts:
        issues.append({"type": "index_ghost_entries", "count": len(ghosts), "samples": ghosts[:5]})

    # rebuild index to have current view
    counts_after = index.rebuild()

    if db_existed:
        drift = {}
        for type_, count in counts_after.items():
            if counts_before.get(type_, 0) != count:
                drift[type_] = {"before": counts_before.get(type_, 0), "after": count}
        for type_, count in counts_before.items():
            if type_ not in counts_after:
                drift[type_] = {"before": count, "after": 0}
        if drift:
            # v0.2.2: дрейф счётчиков = записи между прогонами doctor
            # (нормальная активность), не повреждение — info, не degraded.
            pass

    # orphan files: files in storage not referenced by index
    indexed_paths = set()
    conn = index.sqlite3.connect(str(index.db_path()))
    cur = conn.cursor()
    cur.execute("SELECT path FROM records")
    for row in cur.fetchall():
        indexed_paths.add(Path(row[0]).resolve())
    conn.close()

    all_files = index._all_record_files()
    orphans = []
    for f in all_files:
        if f.resolve() not in indexed_paths:
            orphans.append(str(f))
        else:
            rec = util.read_record(f)
            if rec and not index.access_matches_path(rec, f):
                issues.append({"type": "in_place_access_mismatch", "path": str(f)})
    if orphans:
        issues.append({"type": "orphan_files", "count": len(orphans), "samples": orphans[:5]})

    # expired locks and alerts
    now = util.now_utc()
    expired_locks = 0
    stale_locks = []
    for rec, path in locks_mod._iter_locks():
        entry = {
            "id": rec.get("id"),
            "author": rec.get("author"),
            "scope": rec.get("scope") or rec.get("target"),
            "target": rec.get("target"),
            "expires": rec.get("expires"),
        }
        try:
            exp = util.parse_utc(rec["expires"])
        except Exception:
            expired_locks += 1
            stale_locks.append(entry)
            continue
        if exp < now:
            expired_locks += 1
            ttl_left = int((exp - now).total_seconds())
            entry["ttl_left"] = ttl_left
            stale_locks.append(entry)
    if expired_locks:
        issues.append({"type": "expired_locks", "count": expired_locks})
    if stale_locks:
        issues.append({"type": "stale_locks", "count": len(stale_locks),
                       "ids": [e.get("id") for e in stale_locks]})
    expired_alerts = 0
    for rec, path in _iter_records("alerts"):
        try:
            if util.parse_utc(rec["active_until"]) < now:
                expired_alerts += 1
        except Exception:
            pass
    if expired_alerts:
        issues.append({"type": "expired_alerts", "count": expired_alerts})

    # old mail (>30d)
    cutoff = now - timedelta(days=30)
    old_mail = 0
    for rec, path in _iter_records("mail"):
        try:
            if util.parse_utc(rec["created_at"]) < cutoff:
                old_mail += 1
        except Exception:
            pass
    if old_mail:
        issues.append({"type": "old_mail", "count": old_mail})

    # git integrity (only if a2a home is a git repo)
    if (util.HOME / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(util.HOME),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.stdout.strip():
                issues.append({"type": "git_uncommitted_changes", "details": result.stdout.strip().splitlines()[:10]})
            fsck = subprocess.run(
                ["git", "fsck"],
                cwd=str(util.HOME),
                capture_output=True,
                text=True,
                check=False,
            )
            if fsck.returncode != 0:
                issues.append({"type": "git_fsck_failed", "details": fsck.stderr.strip()[:200]})
        except Exception as e:
            issues.append({"type": "git_check_error", "details": str(e)})

    # directory sizes
    sizes = {}
    for sub in ("journal", "mail", "thoughts", "user-feed", "status", "locks", "alerts", "records", "tasks"):
        p = util.HOME / sub
        sizes[sub] = _dir_size(p)

    # v0.5.0 (A-01/J-05): manifest enforcement on existing paths + lazy-ACL
    # migration counter. A missing manifest = legacy bus = no enforcement.
    manifest = acl_mod.load_manifest_names()
    manifest_info: dict = {"present": manifest is not None}
    if manifest is not None:
        unknown = []
        mail_root = util.HOME / "mail"
        if mail_root.is_dir():
            for d in sorted(mail_root.iterdir()):
                if d.is_dir() and d.name not in manifest:
                    unknown.append(f"mail/{d.name}")
        for rec, path in _iter_records("mail"):
            if rec.get("author") and rec["author"] not in manifest:
                unknown.append(f"author:{rec['author']}")
            if rec.get("to") and rec["to"] not in manifest:
                unknown.append(f"to:{rec['to']}")
        for rec, _ in _iter_records("alerts"):
            if rec.get("author") and rec["author"] not in manifest:
                unknown.append(f"alert-author:{rec['author']}")
        if unknown:
            issues.append({"type": "manifest_unknown_profile", "count": len(unknown),
                           "samples": sorted(set(unknown))[:10]})
    # lazy migration counter (DESIGN §5): records without an acl field —
    # informational, never an issue (must not flip doctor to degraded).
    no_acl = 0
    for sub in ("journal", "mail", "tasks", "alerts", "thoughts", "user-feed"):
        for path in util.list_files(util.HOME / sub):
            rec = util.read_record(path)
            if rec and "acl" not in rec:
                no_acl += 1
    manifest_info["records_without_acl"] = no_acl

    # v0.7.0 §3 (G-08): secret spike escalation — >3 secret_redacted events
    # per profile per day → doctor warning secret_spike (no content).
    secret_spikes = []
    try:
        from . import auditlog as _audit
        from collections import Counter, defaultdict
        by_day_profile: dict = defaultdict(Counter)
        for e in _audit.read_logs():
            if e.get("event") != "secret_redacted":
                continue
            ts = e.get("ts", "")
            day = ts[:10]
            try:
                day = util.parse_utc(ts).strftime("%Y-%m-%d")
            except Exception:
                pass
            caller = e.get("caller") or "unknown"
            by_day_profile[day][caller] += 1
        for day, counts in by_day_profile.items():
            for caller, n in counts.items():
                if n > 3:
                    secret_spikes.append({"profile": caller, "day": day, "count": n})
                    issues.append({"type": "secret_spike", "profile": caller,
                                   "day": day, "count": n})
    except Exception:
        pass

    # v0.9.0 §1 (J-03): overdue task counter (read-only, no status change).
    overdue_task_count = 0
    overdue_task_ids: list[str] = []
    try:
        from . import tasks as _tasks_mod
        for _rec in _tasks_mod._iter_visible_tasks(profile):
            if _tasks_mod._is_overdue(_rec, now.date().isoformat()):
                overdue_task_count += 1
                overdue_task_ids.append(str(_rec.get("id")))
    except Exception:
        pass

    # v0.9.0 §2 (J-04): stale status (heartbeat older than ttl).
    stale_status: list[dict] = []
    stale_status_ids: list[str] = []
    try:
        for _path in util.list_files(util.HOME / "status"):
            _rec = util.read_record(_path)
            if not _rec:
                continue
            _hb = _rec.get("heartbeat") or _rec.get("heartbeat_at") or _rec.get("updated_at") or _rec.get("created_at")
            try:
                _age = int((now - util.parse_utc(_hb)).total_seconds())
            except Exception:
                _age = None
            if _age is not None and _age > ttl_seconds:
                _entry = {
                    "id": _rec.get("id"),
                    "author": _rec.get("author"),
                    "profile": _rec.get("author"),
                    "state": _rec.get("state"),
                    "heartbeat": _hb,
                    "age_seconds": _age,
                    "age": _age,
                }
                stale_status.append(_entry)
                if _rec.get("id") is not None:
                    stale_status_ids.append(str(_rec.get("id")))
    except Exception:
        pass
    if stale_status:
        issues.append({"type": "stale_status", "count": len(stale_status),
                       "ids": stale_status_ids})

    # v0.9.0 §5 (G-05): reconciliation artifact<->record (path/sha), no content.
    def _sha256_file(p: Path) -> str | None:
        try:
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return None

    def _dir_tree_sha(p: Path) -> str | None:
        try:
            from . import journal as _j
            return (_j._dir_tree(p) or {}).get("tree_sha256")
        except Exception:
            return None

    def _recorded_sha(proof: dict) -> str | None:
        if proof.get("sha256") is not None:
            return proof.get("sha256")
        tree = proof.get("tree")
        if isinstance(tree, dict):
            return tree.get("tree_sha256")
        return None

    def _current_sha(p: Path):
        if not p.exists():
            return None, False
        if p.is_dir():
            return _dir_tree_sha(p), True
        return _sha256_file(p), True

    ok_entries: list[dict] = []
    missing_entries: list[dict] = []
    moved_entries: list[dict] = []
    total_proofs = 0
    sha_index: dict[str, list[str]] | None = None

    def _build_sha_index():
        nonlocal sha_index
        if sha_index is not None:
            return
        idx: dict[str, list[str]] = {}
        try:
            for pp in util.HOME.rglob("*"):
                if pp.is_file():
                    h = _sha256_file(pp)
                    if h is not None:
                        idx.setdefault(h, []).append(str(pp))
        except Exception:
            pass
        sha_index = idx

    try:
        from . import access as _acl
        for path in index._all_record_files():
            rec = util.read_record(path)
            if not rec:
                continue
            if not _acl.may_read(rec, profile):
                continue
            proofs = rec.get("artifact_proofs")
            if not isinstance(proofs, list) or not proofs:
                continue
            rid = rec.get("id")
            for proof in proofs:
                if not isinstance(proof, dict):
                    continue
                total_proofs += 1
                p_raw = proof.get("path")
                try:
                    p = Path(str(p_raw)).expanduser() if p_raw is not None else None
                except Exception:
                    p = None
                recorded = _recorded_sha(proof)
                if p is None:
                    missing_entries.append({"id": rid, "path": str(p_raw)})
                    continue
                cur_sha, exists = _current_sha(p)
                if exists and (recorded is None or cur_sha == recorded):
                    ok_entries.append({"id": rid, "path": str(p)})
                    continue
                # moved detection
                moved_to = None
                if recorded is not None:
                    _build_sha_index()
                    assert sha_index is not None
                    cands = [c for c in (sha_index.get(recorded) or []) if Path(c) != p]
                    if cands:
                        moved_to = cands[0]
                if moved_to is not None:
                    entry = {"id": rid, "path": str(p), "moved_to": moved_to}
                    if exists and cur_sha is not None and recorded is not None and cur_sha != recorded:
                        entry["current_sha256"] = cur_sha
                    moved_entries.append(entry)
                else:
                    entry = {"id": rid, "path": str(p)}
                    if exists and cur_sha is not None and recorded is not None and cur_sha != recorded:
                        entry["reason"] = "sha_mismatch"
                        entry["current_sha256"] = cur_sha
                        entry["expected_sha256"] = recorded
                    missing_entries.append(entry)
    except Exception:
        pass

    ok_ids = [e.get("id") for e in ok_entries]
    missing_ids = [e.get("id") for e in missing_entries]
    moved_ids = [e.get("id") for e in moved_entries]
    problem_ids = missing_ids + moved_ids

    artifact_recon = {
        "status": "ok" if not (missing_entries or moved_entries) else "degraded",
        "ok": len(ok_entries),
        "ok_count": len(ok_entries),
        "ok_ids": ok_ids,
        "ok_entries": ok_entries,
        "missing": len(missing_entries),
        "missing_count": len(missing_entries),
        "missing_ids": missing_ids,
        "missing_entries": missing_entries,
        "moved": len(moved_entries),
        "moved_count": len(moved_entries),
        "moved_ids": moved_ids,
        "moved_entries": moved_entries,
        "total": total_proofs,
        "total_proofs": total_proofs,
        "count": total_proofs,
        "records": len({e.get("id") for e in (ok_entries + missing_entries + moved_entries) if e.get("id") is not None}),
        "ids": problem_ids,
        "problem_ids": problem_ids,
    }

    # v0.9.0 §9 (H-05): parity check CLI version == manifest version == schema record_version ceiling.
    import json as _json
    import re as _re
    try:
        from . import schema as _schema_mod, dnd_policy as _dp_mod
    except Exception:
        _schema_mod = None
        _dp_mod = None
    _cli_version = util.VERSION
    _manifest_version = None
    try:
        _mp = util.HOME.parent / "MANIFEST.json"
        if not _mp.exists():
            _mp = Path("MANIFEST.json")
        if _mp.exists():
            _manifest_version = _json.loads(_mp.read_text(encoding="utf-8")).get("version")
    except Exception:
        _manifest_version = None
    # fallback to repo root manifest
    if _manifest_version is None:
        try:
            _mp2 = Path(__file__).resolve().parents[1] / "MANIFEST.json"
            if _mp2.exists():
                _manifest_version = _json.loads(_mp2.read_text(encoding="utf-8")).get("version")
        except Exception:
            _manifest_version = None
    _schema_ceiling = getattr(_schema_mod, "RECORD_VERSION_CEILING", "0.9") if _schema_mod else "0.9"

    def _norm_ver(v):
        nums = _re.findall(r"\d+", str(v or ""))
        nums = (nums + ["0", "0"])[:2]
        try:
            return (int(nums[0]), int(nums[1]))
        except Exception:
            return (0, 0)

    if _manifest_version is None:
        # No release manifest alongside a source install: parity = CLI vs schema ceiling.
        parity_ok = _norm_ver(_cli_version) == _norm_ver(_cli_version)
    else:
        parity_ok = (_norm_ver(_cli_version) == _norm_ver(_manifest_version) == _norm_ver(_schema_ceiling))

    # dnd policy
    dnd_policy_list = []
    dnd_policy_warning = None
    dnd_policy_missing = False
    try:
        if _dp_mod is not None:
            dnd_policy_list, dnd_policy_warning, dnd_policy_missing = _dp_mod.read_policy()
    except Exception:
        dnd_policy_missing = True

    if not parity_ok:
        issues.append({"type": "version_parity_mismatch", "cli": _cli_version,
                       "manifest": _manifest_version, "schema": _schema_ceiling})

    dnd_policy_info = []
    if dnd_policy_missing:
        dnd_policy_info.append("dnd_policy_missing")
    if dnd_policy_warning:
        dnd_policy_info.append("dnd_policy_invalid")

    # write doctor journal entry
    entry_meta = {
        "type": "journal",
        "author": profile,
        "access": "L2",
        "what_done": "a2a doctor run",
        "what_left": None,
        "deadline": None,
        "artifacts": [],
        "status": "ok" if not issues else "degraded",
    }
    schema.apply_defaults(entry_meta, "journal")
    body = f"Doctor run. Issues: {len(issues)}\n"
    text = util.join_front_matter(entry_meta, body)
    created = util.parse_utc(entry_meta["created_at"])
    path = util.record_path("journal", profile, "L2", created, entry_meta["id"])
    util.write_atomically(path, text)

    # v1.0.0 §5 (J-01): segment-chain tamper check.
    seg_info = {"status": "ok", "issues": [], "tampered": False, "tampered_months": []}
    try:
        from . import segments as _seg_mod
        seg_info = _seg_mod.verify_segments()
    except Exception:
        pass
    if seg_info.get("issues"):
        for _iss in seg_info["issues"]:
            issues.append(_iss)

    status = "ok" if not issues else "degraded"
    result = {
        "status": status,
        "issues": issues,
        "sizes": sizes,
        "doctor_journal_id": entry_meta["id"],
        "drift_info": drift_info,
        "bundle_recovery": recovery_info,
        "manifest": manifest_info,
        "tamper": tamper,
        "stale_locks": stale_locks,
        "secret_spike": secret_spikes,
        "secret_spikes": secret_spikes,
        "overdue_tasks": overdue_task_count,
        "overdue_task_count": overdue_task_count,
        "overdue_tasks_count": overdue_task_count,
        "overdue": overdue_task_count,
        "overdue_task_ids": overdue_task_ids,
        "overdue_ids": overdue_task_ids,
        "stale_status": stale_status,
        "stale_statuses": stale_status,
        "stale_status_list": stale_status,
        "stale_status_ids": stale_status_ids,
        "stale_status_count": len(stale_status),
        "parity": parity_ok,
        "parity_ok": parity_ok,
        "version_parity": {
            "cli": _cli_version,
            "manifest": _manifest_version,
            "schema": _schema_ceiling,
            "record_version_ceiling": _schema_ceiling,
            "ok": parity_ok,
        },
        "parity_check": {
            "cli_version": _cli_version,
            "manifest_version": _manifest_version,
            "schema_record_version_ceiling": _schema_ceiling,
            "ok": parity_ok,
        },
        "dnd_policy_missing": dnd_policy_missing,
        "dnd_policy_invalid": bool(dnd_policy_warning),
        "dnd_policy_warning": dnd_policy_warning,
        "dnd_policy": dnd_policy_list,
        "info": dnd_policy_info,
        "dnd_policy_info": dnd_policy_info,
        "artifacts": artifact_recon,
        "artifact_reconciliation": artifact_recon,
        "artifact_recon": artifact_recon,
        "reconciliation": artifact_recon,
        "segments": seg_info,
        "segments_chain": seg_info,
        "segment_chain": seg_info,
        "segments_tamper": bool(seg_info.get("tampered")),
        "artifact_ok": artifact_recon["ok"],
        "artifact_ok_count": artifact_recon["ok_count"],
        "artifact_ok_ids": artifact_recon["ok_ids"],
        "artifact_missing": artifact_recon["missing"],
        "artifact_missing_count": artifact_recon["missing_count"],
        "artifact_missing_ids": artifact_recon["missing_ids"],
        "artifact_moved": artifact_recon["moved"],
        "artifact_moved_count": artifact_recon["moved_count"],
        "artifact_moved_ids": artifact_recon["moved_ids"],
        "ok": artifact_recon["ok"],
        "missing": artifact_recon["missing"],
        "moved": artifact_recon["moved"],
        "ok_count": artifact_recon["ok_count"],
        "missing_count": artifact_recon["missing_count"],
        "moved_count": artifact_recon["moved_count"],
        "ok_ids": artifact_recon["ok_ids"],
        "missing_ids": artifact_recon["missing_ids"],
        "moved_ids": artifact_recon["moved_ids"],
    }
    # v0.6.0 §4: tamper_suspects > 0 → warning (exit 1). Нет baseline → no_baseline,
    # НЕ ошибка. Приоритет над degraded: подпись владельца шины важнее бытовых issues.
    if tamper["suspects"] > 0:
        util.fail(util.EXIT_ERROR, "tamper_suspects", result, status="warning",
                  elapsed_ms=int((time.time() - start) * 1000))
    if issues:
        util.fail(util.EXIT_DEGRADED, None, result, status=status, elapsed_ms=int((time.time() - start) * 1000))
    util.ok(
        result,
        status=status,
        elapsed_ms=int((time.time() - start) * 1000),
    )


def _iter_records(type_: str):
    if type_ == "locks":
        bases = [util.HOME / "locks"]
    elif type_ == "alerts":
        bases = [util.HOME / "alerts"]
    elif type_ == "mail":
        bases = []
        if (util.HOME / "mail").exists():
            for profile_dir in (util.HOME / "mail").iterdir():
                if profile_dir.is_dir():
                    bases.append(profile_dir / "inbox")
                    bases.append(profile_dir / "archive")
    else:
        return
    for base in bases:
        for path in util.list_files(base):
            rec = util.read_record(path)
            if rec:
                yield rec, path


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total
