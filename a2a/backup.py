"""Bus backup/restore (v0.6.0 §5, G-06).

`a2a backup [--dest DIR]` — tar.gz шины (без .state, без самого каталога backups)
с SHA256SUMS.txt внутри; имя файла — по дню (идемпотентно: повтор в тот же день
перезаписывает дневной архив).

`a2a backup --restore FILE [--yes]` — ПО УМОЛЧАНИЮ dry-run: отчёт со списком
конфликтов ничего не меняет на диске. --yes применяет, НО никогда не затирает
файл, чей локальный контент отличается И новее архивного — такие цели уходят в
conflicts и пропускаются. Members с путями-ловушками (абсолютные, ../) — unsafe,
не извлекаются. Целостность: содержимое сверяется с SHA256SUMS.txt внутри архива.
"""

from __future__ import annotations

import hashlib
import os
import tarfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from . import util

EXCLUDE_TOP = {".state", "backups", "wake"}
SUMS_NAME = "SHA256SUMS.txt"


def _iter_bus_files():
    home = util.HOME
    if not home.is_dir():
        return
    for p in sorted(home.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(home)
        if rel.parts and rel.parts[0] in EXCLUDE_TOP:
            continue
        if p.name.endswith(".tmp"):
            continue
        yield p, str(rel)


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile_mkstemp(str(path.parent), path.name + ".", ".tmp")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_name, path)


def tempfile_mkstemp(dir_: str, prefix: str, suffix: str) -> tuple[int, str]:
    import tempfile
    return tempfile.mkstemp(dir=dir_, prefix=prefix, suffix=suffix)


def create(dest: str | None = None) -> dict:
    dest_dir = Path(dest).expanduser() if dest else util.HOME / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"a2a-bus-{util.now_utc().strftime('%Y-%m-%d')}.tar.gz"
    target = dest_dir / name
    tmp = dest_dir / f"{name}.{os.getpid()}.tmp"
    rows: list[str] = []
    count = 0
    with tarfile.open(tmp, "w:gz") as tf:
        for p, rel in _iter_bus_files():
            try:
                data = p.read_bytes()
            except Exception:
                continue
            sha = hashlib.sha256(data).hexdigest()
            ti = tarfile.TarInfo(rel)
            ti.size = len(data)
            ti.mtime = p.stat().st_mtime
            tf.addfile(ti, BytesIO(data))
            rows.append(f"{sha}  {rel}")
            count += 1
        sums = ("\n".join(sorted(rows)) + "\n").encode("utf-8")
        ti = tarfile.TarInfo(SUMS_NAME)
        ti.size = len(sums)
        ti.mtime = time.time()
        tf.addfile(ti, BytesIO(sums))
    os.replace(tmp, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    util.write_atomically(Path(str(target) + ".sha256"), f"{digest}  {name}\n")
    return {"archive": str(target), "records": count,
            "bytes": target.stat().st_size, "sha256": digest}


def _safe_member(name: str) -> bool:
    if not name or name.startswith("/"):
        return False
    parts = Path(name).parts
    return ".." not in parts


def restore(archive: str, yes: bool = False) -> dict:
    apath = Path(archive).expanduser()
    if not apath.is_file():
        util.fail(util.EXIT_USAGE_ERROR, "backup_not_found", {"path": str(apath)})
    try:
        tf = tarfile.open(apath, "r:gz")
    except Exception as exc:
        util.fail(util.EXIT_USAGE_ERROR, "backup_unreadable", {"error": str(exc)})
    report: dict[str, Any] = {"archive": str(apath), "dry_run": not yes,
                              "restored": [], "would_extract": [], "conflicts": [],
                              "skipped_identical": [], "unsafe": [], "integrity_failed": []}
    with tf:
        names = [m.name for m in tf.getmembers() if m.isfile()]
        sums: dict[str, str] = {}
        if SUMS_NAME in names:
            fh = tf.extractfile(SUMS_NAME)
            if fh:
                for line in fh.read().decode("utf-8", "replace").splitlines():
                    if "  " in line:
                        sha, nm = line.split("  ", 1)
                        sums[nm] = sha
        for m in tf.getmembers():
            if not m.isfile() or m.name == SUMS_NAME:
                continue
            if not _safe_member(m.name):
                report["unsafe"].append(m.name)
                continue
            fh = tf.extractfile(m)
            if fh is None:
                report["integrity_failed"].append(m.name)
                continue
            data = fh.read()
            want = sums.get(m.name)
            if want and hashlib.sha256(data).hexdigest() != want:
                report["integrity_failed"].append(m.name)
                continue
            target = util.HOME / m.name
            if target.is_file():
                same = False
                try:
                    same = target.read_bytes() == data
                except Exception:
                    same = False
                if same:
                    report["skipped_identical"].append(m.name)
                    continue
                if target.stat().st_mtime > m.mtime:
                    # контент отличается И диск новее — никогда не затираем
                    report["conflicts"].append({
                        "path": m.name,
                        "disk_mtime": util.utc_iso(datetime_from_ts(target.stat().st_mtime)),
                        "archive_mtime": util.utc_iso(datetime_from_ts(m.mtime)),
                    })
                    continue
            if not yes:
                report["would_extract"].append(m.name)
                continue
            _write_bytes(target, data)
            report["restored"].append(m.name)
    return {
        "archive": report["archive"],
        "dry_run": report["dry_run"],
        "restored": len(report["restored"]),
        "restored_paths": report["restored"],
        "would_extract": len(report["would_extract"]),
        "would_extract_paths": report["would_extract"],
        "conflicts": report["conflicts"],
        "skipped_identical": len(report["skipped_identical"]),
        "unsafe": report["unsafe"],
        "integrity_failed": report["integrity_failed"],
    }


def datetime_from_ts(ts: float):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def cmd_backup(args) -> None:
    start = time.time()
    from . import auditlog
    profile = util.resolve_profile(args)
    restore_target = getattr(args, "restore", None)
    if restore_target:
        yes = bool(getattr(args, "yes", False))
        result = restore(restore_target, yes=yes)
        auditlog.log("backup_restore", profile, str(restore_target),
                     {"dry_run": result["dry_run"], "restored": result["restored"],
                      "conflicts": len(result["conflicts"]), "unsafe": len(result["unsafe"])})
        util.ok(result, elapsed_ms=int((time.time() - start) * 1000))
    result = create(getattr(args, "dest", None))
    auditlog.log("backup_create", profile, result["archive"],
                 {"records": result["records"]})
    util.ok(result, elapsed_ms=int((time.time() - start) * 1000))
