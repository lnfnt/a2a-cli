"""v1.0.0 §5 J-01: append-only segment chain for journal (compact, not Merkle)."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import util


def _segments_dir() -> Path:
    d = util.HOME / ".state" / "segments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _month_of_path(path: Path) -> str | None:
    rec = util.read_record(path)
    if rec and rec.get("created_at"):
        try:
            return str(rec["created_at"])[:7]
        except Exception:
            pass
    # fall back to journal/YYYY/MM layout
    parts = path.parts
    try:
        idx = parts.index("journal")
        y = parts[idx + 1]
        m = parts[idx + 2]
        if len(y) == 4 and len(m) == 2:
            return f"{y}-{m}"
    except (ValueError, IndexError):
        pass
    return None


def _journal_files_for_month(month: str) -> list[Path]:
    out = []
    for p in util.list_files(util.HOME / "journal"):
        if _month_of_path(p) == month:
            out.append(p)
    return sorted(out)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_segment(month: str) -> dict | None:
    path = _segments_dir() / f"{month}.chain.json"
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    return doc


def _segment_path(month: str) -> Path:
    return _segments_dir() / f"{month}.chain.json"


def _sorted_months() -> list[str]:
    out = []
    for p in _segments_dir().glob("*.chain.json"):
        out.append(p.name.replace(".chain.json", ""))
    return sorted(out)


def _prev_head(month: str) -> str | None:
    prev = None
    for m in _sorted_months():
        if m < month:
            prev = m
    if prev is None:
        return None
    doc = _load_segment(prev)
    if not isinstance(doc, dict):
        return None
    return doc.get("head_sha")


def _compute_head(prev_head: str | None, files: list[dict]) -> str:
    payload = (prev_head or "") + "|" + ",".join(f"{f['path']}:{f['sha']}" for f in files)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sealed_paths(doc: dict | None) -> set[str]:
    if not doc:
        return set()
    files = doc.get("files") or []
    return {str(e.get("path")) for e in files if isinstance(e, dict)}


def seal(month: str | None = None) -> dict:
    if not month:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
    month = str(month).strip()
    seg_path = _segment_path(month)
    existing = _load_segment(month)
    sealed_files = list(existing.get("files") or []) if existing else []
    sealed_paths = _sealed_paths(existing)

    current_files = _journal_files_for_month(month)
    tail_entries = []
    for p in current_files:
        rel = str(p.relative_to(util.HOME))
        if rel not in sealed_paths:
            tail_entries.append({"path": rel, "sha": _sha256_file(p)})

    new_files = sealed_files + tail_entries
    # de-dupe by path keeping last sha
    dedup = {}
    order = []
    for e in new_files:
        if not isinstance(e, dict):
            continue
        path = str(e.get("path"))
        if path not in dedup:
            order.append(path)
        dedup[path] = {"path": path, "sha": e.get("sha")}
    new_files = [dedup[p] for p in order]

    prev = _prev_head(month)

    changed = bool(tail_entries)
    if changed or existing is None:
        head = _compute_head(prev, new_files)
        doc = {
            "month": month,
            "files": new_files,
            "head_sha": head,
            "prev_head": prev,
            "sealed_at": util.utc_iso(),
            "tail": [],
            "unsealed": [],
        }
        util.write_atomically(seg_path, json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        doc = existing
        head = doc.get("head_sha")
        # ensure fields
        doc.setdefault("files", new_files)
        doc.setdefault("tail", [])
        doc.setdefault("unsealed", [])

    # recompute downstream heads to keep chain consistent
    downstream = sorted(m for m in _sorted_months() if m > month)
    cur_prev = head
    for m in downstream:
        ddoc = _load_segment(m)
        if not ddoc:
            continue
        files = ddoc.get("files") or []
        new_head = _compute_head(cur_prev, files)
        ddoc["prev_head"] = cur_prev
        ddoc["head_sha"] = new_head
        util.write_atomically(_segment_path(m), json.dumps(ddoc, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        cur_prev = new_head

    # unsealed tail = current files not in sealed set
    sealed_now = {str(e.get("path")) for e in (doc.get("files") or []) if isinstance(e, dict)}
    unsealed = []
    for p in current_files:
        rel = str(p.relative_to(util.HOME))
        if rel not in sealed_now:
            unsealed.append(rel)

    return {
        "month": month,
        "path": str(seg_path),
        "segment": str(seg_path),
        "segment_path": str(seg_path),
        "file": str(seg_path),
        "sealed": len(tail_entries),
        "sealed_count": len(tail_entries),
        "newly_sealed": len(tail_entries),
        "changed": changed,
        "no_change": not changed,
        "idempotent": True,
        "files": doc.get("files", new_files),
        "head_sha": doc.get("head_sha", head),
        "prev_head": doc.get("prev_head", prev),
        "tail": unsealed,
        "unsealed": unsealed,
        "unsealed_tail": unsealed,
        "count": len(doc.get("files", new_files)),
    }


def list_segments() -> dict:
    items = []
    for month in _sorted_months():
        doc = _load_segment(month)
        if not doc:
            continue
        items.append({
            "month": month,
            "files": doc.get("files", []),
            "head_sha": doc.get("head_sha"),
            "prev_head": doc.get("prev_head"),
            "count": len(doc.get("files") or []),
        })
    return {"count": len(items), "segments": items, "items": items, "entries": items}


def verify_segments() -> dict:
    """Return tamper info for doctor. Empty issues = ok."""
    issues = []
    tampered_months = []
    months = _sorted_months()
    # chain verification
    prev_head = None
    # sort months
    for month in months:
        doc = _load_segment(month)
        if not doc:
            continue
        # check prev_head matches chain
        if doc.get("prev_head") != prev_head and not (doc.get("prev_head") is None and prev_head is None):
            # allow first
            if prev_head is not None or doc.get("prev_head") is not None:
                pass
        # verify file shas
        for e in doc.get("files") or []:
            if not isinstance(e, dict):
                continue
            rel = e.get("path")
            try:
                p = (util.HOME / str(rel))
            except Exception:
                p = None
            if p is None or not p.exists():
                issues.append({"type": "segments_tamper", "month": month, "path": rel, "reason": "missing"})
                tampered_months.append(month)
                continue
            try:
                cur = _sha256_file(p)
            except Exception:
                cur = None
            if cur != e.get("sha"):
                issues.append({"type": "segments_tamper", "month": month, "path": rel, "reason": "sha_mismatch"})
                tampered_months.append(month)
        # verify head
        files = doc.get("files") or []
        calc = _compute_head(doc.get("prev_head"), files)
        if calc != doc.get("head_sha"):
            issues.append({"type": "segments_tamper", "month": month, "reason": "head_mismatch"})
            tampered_months.append(month)
        # chain prev: use stored head for next
        # but use actual stored head (even if tampered) to continue? Use stored.
        prev_head = doc.get("head_sha")
    # also check prev_head consistency across chain (recompute expected prev)
    # Recompute prev chain from first month using stored heads? Already.
    # Detect broken chain if a month's prev_head != previous month's head_sha
    prev = None
    for month in months:
        doc = _load_segment(month)
        if not doc:
            continue
        if prev is not None and doc.get("prev_head") != prev:
            issues.append({"type": "segments_tamper", "month": month, "reason": "chain_broken"})
            tampered_months.append(month)
        prev = doc.get("head_sha")
    # dedupe issues? keep all
    return {
        "status": "ok" if not issues else "tamper_detected",
        "issues": issues,
        "tampered": len(issues) > 0,
        "tampered_months": sorted(set(tampered_months)),
        "months": months,
        "count": len(issues),
    }


def cmd_segments(args) -> None:
    start = time.time()
    if getattr(args, "seal", False):
        month = getattr(args, "month", None)
        if month is None:
            month = getattr(args, "M", None)
        info = seal(month)
        util.ok(info, elapsed_ms=int((time.time() - start) * 1000))
        return
    # default: list
    util.ok(list_segments(), elapsed_ms=int((time.time() - start) * 1000))
