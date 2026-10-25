"""Verifiable export (v0.9.0 §10, B-06)."""

from __future__ import annotations

import hashlib
import json
import time
from argparse import Namespace
from datetime import datetime
from pathlib import Path

from . import access as acl_mod, util


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()




def _normalize(obj):
    if isinstance(obj, datetime):
        return util.utc_iso(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return obj

def _find_record(ref: str):
    for path in util.list_files(util.HOME):
        if not path.suffix == ".md":
            continue
        rec = util.read_record(path)
        if rec and str(rec.get("id")) == str(ref):
            return rec, path
    return None, None


def _artifact_shas(rec: dict) -> list[dict]:
    out = []
    proofs = rec.get("artifact_proofs")
    if not isinstance(proofs, list):
        proofs = rec.get("artifacts")
    if not isinstance(proofs, list):
        proofs = []
    for item in proofs:
        if isinstance(item, dict):
            path = item.get("path") or item.get("artifact") or item.get("file")
            sha = item.get("sha256")
            if sha is None:
                tree = item.get("tree")
                if isinstance(tree, dict):
                    sha = tree.get("tree_sha256")
            if sha is None and path:
                try:
                    p = Path(str(path)).expanduser()
                    if p.exists() and p.is_file():
                        sha = hashlib.sha256(p.read_bytes()).hexdigest()
                except Exception:
                    sha = None
            out.append({"path": path, "sha256": sha})
        elif isinstance(item, str):
            sha = None
            try:
                p = Path(item).expanduser()
                if p.exists() and p.is_file():
                    sha = hashlib.sha256(p.read_bytes()).hexdigest()
            except Exception:
                sha = None
            out.append({"path": item, "sha256": sha})
    return out


def cmd_export(args: Namespace) -> None:
    start = time.time()
    profile = util.resolve_profile(args)
    verify_file = getattr(args, "verify", None)
    if verify_file:
        _verify(Path(verify_file), start)
        return

    ref = getattr(args, "ref", None)
    out = getattr(args, "out", None)
    if not ref or not out:
        util.fail(util.EXIT_USAGE_ERROR, "export_requires_ref_and_out",
                  {"hint": "a2a export --ref <id> --out FILE"})
        return

    rec, path = _find_record(ref)
    if not rec:
        util.fail(util.EXIT_USAGE_ERROR, "record_not_found", {"ref": ref})
        return
    try:
        if not acl_mod.may_read(rec, profile):
            util.fail(util.EXIT_USAGE_ERROR, "access_denied", {"ref": ref})
            return
    except Exception:
        pass
    if rec.get("access") == "L1" and str(rec.get("author")) != str(profile):
        util.fail(util.EXIT_USAGE_ERROR, "access_denied", {"ref": ref})
        return

    export_rec = _normalize(dict(rec))
    export_rec.pop("_path", None)

    payload = {
        "format": "a2a-export",
        "version": "0.9",
        "ref": ref,
        "id": rec.get("id"),
        "type": rec.get("type"),
        "author": rec.get("author"),
        "access": rec.get("access"),
        "created_at": _normalize(rec.get("created_at")),
        "ts": util.utc_iso(),
        "record": export_rec,
        "record_sha256": _sha256_text(_canonical(export_rec)),
        "artifacts": _artifact_shas(rec),
        "signature": None,
    }
    payload["export_sha256"] = _sha256_text(_canonical({k: v for k, v in payload.items() if k != "export_sha256"}))

    out_path = Path(out).expanduser()
    util.ensure_dir(out_path)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    util.ok({
        "ref": ref,
        "out": str(out_path),
        "path": str(out_path),
        "file": str(out_path),
        "exported": True,
        "record_sha256": payload["record_sha256"],
        "export_sha256": payload["export_sha256"],
        "artifacts": payload["artifacts"],
    }, elapsed_ms=int((time.time() - start) * 1000))


def _verify(path: Path, start: float) -> None:
    if not path.exists() or not path.is_file():
        util.fail(util.EXIT_USAGE_ERROR, "export_file_not_found", {"path": str(path)})
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        util.fail(util.EXIT_USAGE_ERROR, "invalid_export_file", {"path": str(path)})
        return
    if not isinstance(data, dict):
        util.fail(util.EXIT_USAGE_ERROR, "invalid_export_file", {"path": str(path)})
        return

    ok = True
    reasons = []
    record = data.get("record")
    if not isinstance(record, dict):
        ok = False
        reasons.append("missing_record")
    else:
        if _sha256_text(_canonical(record)) != data.get("record_sha256"):
            ok = False
            reasons.append("record_sha_mismatch")

    payload_without_hash = {k: v for k, v in data.items() if k != "export_sha256"}
    if _sha256_text(_canonical(payload_without_hash)) != data.get("export_sha256"):
        ok = False
        reasons.append("export_sha_mismatch")

    if not data.get("export_sha256"):
        ok = False
        reasons.append("missing_export_sha")

    if ok:
        util.ok({
            "verified": True,
            "ok": True,
            "valid": True,
            "path": str(path),
            "ref": data.get("ref") or data.get("id"),
            "id": data.get("id"),
        }, elapsed_ms=int((time.time() - start) * 1000))
    else:
        util.fail(util.EXIT_ERROR, "export_verify_failed", {
            "verified": False,
            "ok": False,
            "valid": False,
            "path": str(path),
            "reasons": reasons,
        })
