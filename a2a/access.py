"""Record-level ACL + profile manifest (DESIGN-A2A-V05 §4, v0.5.0).

Two orthogonal concepts:
- access level (L1/L2/L3) — v0.4.10 contract: L1 visible to author only,
  foreign L1 via --include-l1 with an audit event. UNCHANGED for records
  without an `acl` field (no narrowing of pre-existing queries);
- `acl` list on a record — explicit grants: profile names, `@all-lex`
  (any profile in the manifest), `@owner` (record's owner/author).
  Author/owner/recipient are always implicitly readable.

profiles.json (bus root): identity manifest. Enforcement is only active
when the manifest exists — legacy buses without it behave exactly as v0.4.10.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import util

MANIFEST_NAME = "profiles.json"

# user-feed is a publication channel, not a sender; listed in the manifest
# as a channel. Known agent profiles at v0.5.0 introduction:
DEFAULT_PEER_PROFILES = (
    "coordinator",
    "colleague",
    "user-profile",
    "support",
    "agent-profile",
    "infra",
)
DEFAULT_CHANNELS = ("user-feed",)


def manifest_path() -> Path:
    return util.HOME / MANIFEST_NAME


def load_manifest_names() -> set[str] | None:
    """Return known profile names, or None when no manifest governs this bus.

    A corrupt manifest is treated as 'no manifest' for enforcement (fail
    open on reads, doctor flags the corruption as an issue) — refusing to
    run the whole bus because one JSON file broke would be worse.
    """
    path = manifest_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        return None
    names: set[str] = set()
    for entry in profiles:
        if isinstance(entry, str):
            names.add(entry)
        elif isinstance(entry, dict) and entry.get("name"):
            names.add(str(entry["name"]))
    return names


def manifest_known(name: str) -> bool | None:
    """True/False when a manifest governs, None when it does not exist."""
    names = load_manifest_names()
    if names is None:
        return None
    return name in names


def enforce_known(name: str, role: str) -> None:
    """Refuse unknown profiles when the bus has a manifest (A-01).

    Self-declaration via env stays, but 'typo'd a name -> wrote into the
    world' is closed: unknown author/recipient/owner = refusal.
    """
    known = manifest_known(name)
    if known is False:
        util.fail(util.EXIT_USAGE_ERROR, f"unknown_profile:{role}:{name}")


def _implicit_grantees(rec: dict[str, Any]) -> set[str]:
    grants = set()
    for key in ("author", "owner", "to"):
        val = rec.get(key)
        if val:
            grants.add(str(val))
    return grants


def may_read(rec: dict[str, Any], profile: str, include_l1: bool = False) -> bool:
    """Single read predicate for all read commands (journal/mail/alerts/tasks).

    - record with explicit acl: only acl tokens + implicit author/owner/to
      may read; --include-l1 does NOT bypass an explicit acl;
    - record without acl: v0.4.10 semantics preserved exactly —
      L1 readable by author, foreign L1 only with include_l1 (caller must
      log the audit event itself, as journal/read/search already do).
    """
    acl = rec.get("acl")
    if isinstance(acl, list) and acl:
        tokens = {str(t) for t in acl}
        if profile in tokens or "@all-lex" in tokens:
            return True
        if "@owner" in tokens:
            owner = str(rec.get("owner") or rec.get("author") or "")
            if profile == owner:
                return True
        if profile in _implicit_grantees(rec):
            return True
        return False
    if rec.get("access") == "L1":
        if profile in _implicit_grantees(rec):
            return True
        return bool(include_l1)
    return True


def acl_only(rec: dict[str, Any], profile: str) -> bool:
    """acl gate for path-scoped listings (mail inbox/outbox) where legacy
    semantics imposed no access-level filter: only an explicit acl blocks."""
    acl = rec.get("acl")
    if isinstance(acl, list) and acl:
        return may_read(rec, profile)
    return True


def write_default_manifest(path: Path | None = None) -> Path:
    """Create profiles.json at the bus root (idempotent helper for setup)."""
    target = path or manifest_path()
    payload = {
        "profiles": [
            {"name": name, "role": "peer-agent"} for name in DEFAULT_PEER_PROFILES
        ],
        "channels": list(DEFAULT_CHANNELS),
    }
    util.write_atomically(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return target
