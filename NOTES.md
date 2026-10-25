# NOTES.md — a2a-cli

## ID generation

ULID-like, offline, monotonic:

```
<integer_ms_since_epoch in hex, 13 chars><uuid4 hex suffix, 19 chars>
```

Example: `synthetic-id` (32 hex chars total).

Properties:
- Sortable by creation time.
- No network or external dependency.
- Collision probability is UUID4-level for the suffix.

## Deviations / clarifications from SPEC.md

1. **Mail physical layout**: SPEC §5.2 says "Доставка = атомарный move". For MVP we keep a physical copy in the recipient `inbox/` and a separate copy in the sender `outbox/` (atomic `os.replace`). The derived SQLite index indexes only the canonical `inbox/` (and `archive/`) copies; `outbox/` is treated as a non-indexed sender mirror.
2. **DND representation**: SPEC §6 shows DND as a `status` record with `state: dnd`. Implemented exactly that way; `a2a dnd set|clear|check` are convenience wrappers around the status file.
3. **Alert CLI**: `a2a alert set` and `a2a alert list` are exposed as explicit commands. SPEC §7 does not list them, but the MVP scope requires `alerts.py` and the type is defined in SPEC §5.6.
4. **User note type name**: Stored with `type: user-note` (SPEC §5.8). CLI command is `a2a user post`.
5. **Orphan definition**: In `a2a doctor`, an orphan is any `.md` file under the storage tree that `util.read_record()` cannot parse as front-matter YAML.
6. **Verify exit code**: Schema violations detected by `a2a verify` return exit code `3` (`INVALID_RECORD`), matching SPEC §8.
7. **Index backend**: SQLite `fts5` virtual table is used for full-text search; the `.state/index.sqlite3` file is derived and rebuildable.

## Open questions

- Should `mail send` use a single canonical file with symlinks instead of two copies? Current copy approach is simpler and avoids symlink edge cases but doubles storage for mail.
- Should `a2a read --unread` also filter non-mail records? Currently it only filters `type: mail` records by `msg_status`; other record types are always included.
- Should `verify` validate that every indexed record path actually exists on disk? Currently it walks the filesystem.
