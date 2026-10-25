"""Bus tamper-detection baseline (v0.6.0 §4, G-04).

`a2a snapshot --save` — ЕДИНСТВЕННЫЙ писатель .state/bus_hashes.json:
канонический sha256-снимок всех записей mail/journal/tasks/alerts с id записей.
Легитимные записи после snapshot baseline НЕ двигают (иначе tamper не ловится) —
обновлять baseline нужно явно, регулярно (README: после приёмки каждого релиза
или не реже конца рабочей смены).

`a2a doctor` сверяет текущие хэши с baseline и перечисляет changed/deleted/added
путь+id — НИКОГДА содержимое файлов (PII не попадает в отчёты).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import util

SCAN_DIRS = ("mail", "journal", "tasks", "alerts")
BASELINE_NAME = "bus_hashes.json"


def baseline_path() -> Path:
    return util.HOME / ".state" / BASELINE_NAME


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect() -> dict:
    """relpath -> {"sha256":..., "id":...} по всем записям сканируемых областей."""
    out: dict[str, dict] = {}
    for sub in SCAN_DIRS:
        base = util.HOME / sub
        if not base.is_dir():
            continue
        for path in util.list_files(base):
            try:
                data = path.read_bytes()
            except Exception:
                continue
            id_ = None
            try:
                parsed = util.split_front_matter(data.decode("utf-8-sig"))
                if parsed:
                    id_ = parsed[0].get("id")
            except Exception:
                id_ = None
            out[str(path.relative_to(util.HOME))] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "id": str(id_) if id_ else None,
            }
    return out


def save() -> dict:
    files = collect()
    doc = {"version": 1, "saved_at": util.utc_iso(), "files": files}
    path = baseline_path()
    util.write_atomically(path, json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"baseline": str(path), "records": len(files), "saved_at": doc["saved_at"]}


def load_baseline() -> dict | None:
    path = baseline_path()
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), dict):
        return None
    return doc


def compare() -> dict:
    """Сверка с baseline; содержимое файлов в отчёт НЕ попадает (только путь+id)."""
    baseline = load_baseline()
    if baseline is None:
        return {"status": "no_baseline", "baseline_saved_at": None,
                "changed": [], "deleted": [], "added": [], "suspects": 0}
    current = collect()
    old_files = baseline["files"]
    changed, deleted, added = [], [], []
    for rel, meta in sorted(old_files.items()):
        cur = current.get(rel)
        if cur is None:
            deleted.append({"path": rel, "id": meta.get("id")})
        elif cur.get("sha256") != meta.get("sha256"):
            changed.append({"path": rel, "id": meta.get("id")})
    for rel, meta in sorted(current.items()):
        if rel not in old_files:
            added.append({"path": rel, "id": meta.get("id")})
    suspects = len(changed) + len(deleted) + len(added)
    return {
        "status": "clean" if suspects == 0 else "tamper_detected",
        "baseline_saved_at": baseline.get("saved_at"),
        "changed": changed,
        "deleted": deleted,
        "added": added,
        "suspects": suspects,
    }


def cmd_snapshot(args) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    from . import auditlog
    if getattr(args, "save", False):
        info = save()
        auditlog.log("snapshot_save", profile, info["baseline"],
                     {"records": info["records"]})
        util.ok(info, elapsed_ms=int((time.time() - start) * 1000))
    util.ok(compare(), elapsed_ms=int((time.time() - start) * 1000))
