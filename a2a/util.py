"""Common utilities for a2a."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

VERSION = __version__

_PROFILE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# One documented shared store; A2A_HOME is only an explicit override.
DEFAULT_HOME = Path.home() / ".a2a"


def resolve_home(environ: dict[str, str] | None = None) -> Path:
    """Resolve the shared store, treating unset/empty A2A_HOME identically."""
    values = os.environ if environ is None else environ
    raw = values.get("A2A_HOME", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_HOME


HOME = resolve_home()
HOME_CONFIGURATION_ERROR = "a2a_home_not_directory" if HOME.exists() and not HOME.is_dir() else None

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DEGRADED = 2
EXIT_INVALID_RECORD = 3
EXIT_USAGE_ERROR = 4
EXIT_TIMEOUT = 124


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: datetime | None = None) -> str:
    if dt is None:
        dt = now_utc()
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    value = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    # v0.4.10 (review R8): naive-даты текли вниз и падали на сравнении с
    # aware now_utc(). Контракт шины: все даты — aware UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def new_id() -> str:
    # ULID-like monotonic: timestamp ms + random suffix
    return f"{int(time.time() * 1000):013x}{uuid.uuid4().hex[:19]}"


def validate_profile_name(name: str) -> bool:
    """Return True if name is a safe profile/addressee identifier."""
    return bool(_PROFILE_RE.fullmatch(name))


def resolve_profile(args: Any) -> str:
    """Определить профиль вызывающего (v0.2.1: каскад автодетекта).

    Приоритет: флаг команды > A2A_PROFILE > HERMES_PROFILE >
    HERMES_HOME (имя каталога) > ~/.hermes/active_profile > 'default'.

    HERMES_HOME добавлен в v0.2.6: в режиме профилей Hermes выставляет
    HERMES_HOME=~/.hermes/profiles/<имя>, но НЕ выставляет HERMES_PROFILE.
    Каскад читал только HERMES_PROFILE, падал на устаревший
    ~/.hermes/active_profile и молча адресовал почту чужому профилю —
    письмо не находилось (mail_not_found) либо уходило не туда.
    """
    # --profile сильнее env (явное указание), env сильнее --as (легаси-контракт)
    candidate = None
    if getattr(args, "profile", None):
        candidate = args.profile
    if candidate is None:
        env_profile = os.environ.get("A2A_PROFILE") or os.environ.get("HERMES_PROFILE")
        if env_profile:
            candidate = env_profile
    if candidate is None and getattr(args, "as_", None):
        candidate = args.as_
    if candidate is None:
        # Профильный режим Hermes: ~/.hermes/profiles/<имя>. Имя каталога и есть
        # профиль. Актуальнее маркерного файла, поэтому проверяется раньше него.
        hermes_home = os.environ.get("HERMES_HOME") or ""
        name = Path(hermes_home).name if hermes_home else ""
        if name and name != "profiles":
            candidate = name
    if candidate is None:
        marker = Path.home() / ".hermes" / "active_profile"
        try:
            if marker.exists():
                name = marker.read_text(encoding="utf-8").strip()
                if name:
                    candidate = name
        except Exception:
            pass
    if candidate is None:
        candidate = "default"
    if not validate_profile_name(candidate):
        fail(EXIT_USAGE_ERROR, "invalid_profile_name")
    # v0.5.0 (A-01): self-declaration stays, but when the bus carries a
    # profiles manifest an unknown profile is refused — the class of
    # "typo'd a name, wrote into the world" closes here.
    try:
        from . import access  # late import: util is imported by access
    except ImportError:  # pragma: no cover
        return candidate
    access.enforce_known(candidate, "caller")
    return candidate


def fail(
    code: int,
    reason: str | None,
    data: Any = None,
    status: str | None = None,
    elapsed_ms: int = 0,
    record: bool = True,
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    if data is None:
        data = {}
    metadata: dict[str, Any] = {"version": VERSION, "elapsed_ms": elapsed_ms}
    # v0.5.0: idempotency dup-flags and similar surface in envelope metadata.
    # Absent by default — pre-v0.5 envelopes are byte-identical to before.
    if extra_metadata:
        metadata.update(extra_metadata)
    envelope = {
        "ok": code == EXIT_OK,
        "status": status or ("ok" if code == EXIT_OK else "error"),
        "reason": reason,
        "data": data,
        "metadata": metadata,
    }
    print(json.dumps(envelope, ensure_ascii=False, sort_keys=True))
    raise SystemExit(code)


def ok(data: Any = None, status: str = "ok", reason: str | None = None, elapsed_ms: int = 0,
       extra_metadata: dict[str, Any] | None = None) -> None:
    fail(EXIT_OK, reason, data, status, elapsed_ms, extra_metadata=extra_metadata)


def fail_hint(
    code: int,
    reason: str | None,
    hint: str | None = None,
    example: str | None = None,
    data: Any = None,
    status: str | None = None,
    elapsed_ms: int = 0,
    record: bool = True,
    extra_metadata: dict[str, Any] | None = None,
    **extra_data: Any,
) -> None:
    """Like fail(), but guarantees data carries hint + example (§4 I-01)."""
    if data is None:
        payload: dict[str, Any] = {}
    elif isinstance(data, dict):
        payload = dict(data)
    else:
        payload = {"data": data}
    payload.update(extra_data)
    if hint is not None:
        payload["hint"] = hint
    if example is not None:
        payload["example"] = example
    fail(code, reason, payload, status=status, elapsed_ms=elapsed_ms, record=record,
         extra_metadata=extra_metadata)


def split_front_matter(text: str) -> tuple[dict[str, Any], str] | None:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not m:
        return None
    import yaml

    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return None
    body = m.group(2)
    return meta, body


def join_front_matter(meta: dict[str, Any], body: str) -> str:
    import yaml

    lines = ["---"]
    lines.append(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).rstrip("\n"))
    lines.append("---")
    if body and not body.endswith("\n"):
        body += "\n"
    lines.append(body)
    return "\n".join(lines)


def record_path(type_: str, profile: str, access: str, created: datetime, id_: str) -> Path:
    date = created.strftime("%Y-%m-%d")
    if type_ == "journal":
        return HOME / "journal" / created.strftime("%Y") / created.strftime("%m") / f"{date}_{profile}_{id_}.md"
    if type_ == "thought":
        return HOME / "thoughts" / profile / access.lower() / f"{date}_{id_}.md"
    if type_ == "mail":
        return HOME / "mail" / profile / "outbox" / f"{date}_{id_}.md"
    if type_ == "status":
        return HOME / "status" / f"{profile}.md"
    if type_ == "lock":
        return HOME / "locks" / f"{profile}_{id_}.md"
    if type_ == "alert":
        return HOME / "alerts" / f"{date}_{profile}_{id_}.md"
    if type_ == "handoff":
        return HOME / "records" / "handoffs" / f"{date}_{profile}_{id_}.md"
    if type_ == "user-note":
        return HOME / "user-feed" / f"{date}_{profile}_{id_}.md"
    raise ValueError(f"unknown type {type_}")


def ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_atomically(path: Path, content: str) -> None:
    """Атомарная запись с fsync (v0.4.1, P0-6).

    tmp -> fsync(file) -> rename -> fsync(dir): переживает обрыв питания
    без потери уже подтверждённой записи и без «половины файла».
    """
    ensure_dir(path)
    # v0.4.9 (sa-0 F «tmp race»): уникальный tmp (mkstemp) — детерминированное
    # имя сталкивалось при параллельных writer'ах и падало ПОСЛЕ доставки.
    # Контракт v0.4.1 сохранён: tmp остаётся на диске при сбое rename для диагностики.
    import tempfile
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_name, path)
    try:
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass  # fsync каталога — best-effort (не все ФС поддерживают)


def list_files(path: Path, pattern: str = "*.md") -> list[Path]:
    if not path.exists():
        return []
    return sorted(p for p in path.rglob(pattern) if p.is_file())


def read_record(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception:
        return None
    parsed = split_front_matter(text)
    if parsed is None:
        return None
    meta, body = parsed
    meta["_path"] = str(path)
    meta["_body"] = body
    return meta


# ---------------------------------------------------------------------------
# v0.5.0 (DESIGN-A2A-V05 §2): bundle-транзакция — commit_bundle + recovery.
# ---------------------------------------------------------------------------

def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass  # не все ФС поддерживают — как в write_atomically


def bundle_txn_dir() -> Path:
    return HOME / ".state" / "bundle_txn"


def _write_txn(txn_path: Path, doc: dict) -> None:
    write_atomically(txn_path, json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def commit_bundle(ops: "list[tuple[Path, str]]", label: str = "bundle", bundle_id: str | None = None) -> list[Path]:
    """Atomically land a set of (path, content) files.

    Protocol (forward-recovery, no rollback by design — a killed process is
    finished off by `a2a doctor`, idempotently):
      1. every payload lands in a unique mkstemp next to its target, fsynced;
      2. a manifest journal `<HOME>/.state/bundle_txn/<id>.bundle_txn`
         (staged tmp -> target, sha256 per op) is written and fsynced BEFORE
         any rename — "до";
      3. one rename wave commits the files;
      4. journal flips to "committed" and is removed — "после".
    A crash between 2 and 4 leaves tmps + journal; recover_bundles()
    completes the wave. Nothing is ever half-written: each rename is atomic.
    """
    if not ops:
        return []
    if not bundle_id:
        bundle_id = new_id()
    staged: list[dict] = []
    for path, content in ops:
        path = Path(path)
        ensure_dir(path)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".bundle-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        staged.append(
            {
                "target": str(path),
                "tmp": tmp_name,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    txn_dir = bundle_txn_dir()
    txn_dir.mkdir(parents=True, exist_ok=True)
    txn_path = txn_dir / f"{bundle_id}.bundle_txn"
    doc = {"id": bundle_id, "label": label, "created_at": utc_iso(), "state": "pending", "ops": staged}
    _write_txn(txn_path, doc)

    try:
        for op in staged:
            os.replace(op["tmp"], op["target"])
        for parent in {str(Path(op["target"]).parent) for op in staged}:
            _fsync_dir(Path(parent))
    except OSError:
        # forward-recovery: journal + surviving tmps stay on disk for doctor;
        # the exception propagates so the caller reports failure honestly.
        doc["state"] = "pending"
        _write_txn(txn_path, doc)
        raise
    doc["state"] = "committed"
    _write_txn(txn_path, doc)
    try:
        txn_path.unlink()
    except FileNotFoundError:
        pass
    _fsync_dir(txn_dir)
    return [Path(op["target"]) for op in staged]


def recover_bundles() -> dict:
    """Finish interrupted bundle commits. Idempotent: a second run with a
    clean journal directory does nothing and returns zero counters."""
    txn_dir = bundle_txn_dir()
    result = {"scanned": 0, "applied_ops": 0, "cleaned_tmps": 0, "unrecoverable": [], "bundles_pending": 0}
    if not txn_dir.is_dir():
        return result
    for txn_path in sorted(txn_dir.glob("*.bundle_txn")):
        result["scanned"] += 1
        try:
            doc = json.loads(txn_path.read_text(encoding="utf-8"))
        except Exception:
            result["unrecoverable"].append({"txn": str(txn_path), "reason": "corrupt_bundle_txn"})
            result["bundles_pending"] += 1
            continue
        unrecoverable_here = False
        for op in doc.get("ops", []):
            target = Path(op.get("target", ""))
            tmp = Path(op.get("tmp", ""))
            want = op.get("sha256", "")
            target_ok = False
            if target.is_file():
                try:
                    target_ok = hashlib.sha256(target.read_text(encoding="utf-8").encode("utf-8")).hexdigest() == want
                except Exception:
                    target_ok = False
            if target_ok:
                if tmp.is_file():
                    try:
                        tmp.unlink()
                        result["cleaned_tmps"] += 1
                    except OSError:
                        pass
                continue
            if tmp.is_file():
                ensure_dir(target)
                try:
                    os.replace(tmp, target)
                    _fsync_dir(target.parent)
                    result["applied_ops"] += 1
                except OSError as exc:
                    result["unrecoverable"].append({"target": str(target), "reason": str(exc)})
                    unrecoverable_here = True
            else:
                # neither tmp nor correct target survived — honest report,
                # never fabricate data
                result["unrecoverable"].append({"target": str(target), "reason": "missing_staged_content"})
                unrecoverable_here = True
        if not unrecoverable_here:
            try:
                txn_path.unlink()
            except FileNotFoundError:
                pass
            _fsync_dir(txn_dir)
        else:
            doc["state"] = "recovery_failed"
            _write_txn(txn_path, doc)
            result["bundles_pending"] += 1
    return result
