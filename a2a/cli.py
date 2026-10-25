"""a2a CLI entry point."""

from __future__ import annotations

import argparse
import os
import sys
import time
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import (
    scrubber,
    alerts,
    cards as cards_mod,
    digest as digest_mod,
    doctor,
    sessions as sessions_mod,
    handoff,
    index,
    owner as owner_mod,
    quarantine as quarantine_mod,
    prune as prune_mod,
    metrics as metrics_mod,
    export as export_mod,
    journal,
    migrate as migrate_mod,
    locks,
    mail,
    userfeed,
    rpc,
    schema,
    snapshot as snapshot_mod,
    backup as backup_mod,
    cookbook as cookbook_mod,
    segments as segments_mod,
    status,
    tasks as tasks_mod,
    thoughts,
    util,
    secrets as secrets_mod,
)


def build_parser() -> argparse.ArgumentParser:
    access_parent = argparse.ArgumentParser(add_help=False)
    access_parent.add_argument("--access", default=None, help="Override access level")

    parser = argparse.ArgumentParser(prog="a2a", description="Agent-to-agent CLI for agents")
    parser.add_argument("--profile", default=None, help="Caller profile")
    parser.add_argument("--as", dest="as_", default=None, help="Act as profile")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("log", help="Log a journal entry", parents=[access_parent])
    p.add_argument("what_done")
    p.add_argument("--left", default=None)
    p.add_argument("--artifacts", default=None)
    p.add_argument("--deadline", default=None)
    p.add_argument("--verify", default=None)
    p.add_argument("--verify-run", action="store_true", help="Run verify command and mark VERIFY_FAILED on non-zero exit")
    p.add_argument("--numbers-ok", action="store_true", help="v0.4.7 poka-yoke bypass: сознательно записать числа без проганной --verify")
    p.add_argument("--status", default=None)
    p.add_argument("--session", default=None, help="Event-sourcing: привязать запись к id сессии Hermes (v0.4.1)")
    p.add_argument("--tags", default=None, help="Comma-separated tags for the journal entry")
    p.add_argument("--private", action="store_true", help="Private (L1) journal entry")

    p = sub.add_parser("journal", help="Show journal entries")
    p.add_argument("--today", action="store_true")
    p.add_argument("--week", action="store_true")
    p.add_argument("--author", default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--mine", action="store_true", help="Only entries authored by current profile")
    p.add_argument("--private", action="store_true", help="Only private (L1) entries")
    p.add_argument("--include-l1", action="store_true", help="чужие L1-записи с audit-событием (v0.4.10)")
    p.add_argument("--session", default=None, help="Filter by session id (event-sourcing replay)")
    p.add_argument("--min-seq", dest="min_seq", default=None, help="Only entries with seq >= N (incremental sync)")

    p = sub.add_parser("mail", help="Mail commands")
    p.add_argument("--inbox", action="store_true", help="List inbox (bare `a2a mail` = inbox list)")
    p.add_argument("--outbox", action="store_true", help="List outbox")
    p.add_argument("--unread", action="store_true", help="Only unread")
    mail_sub = p.add_subparsers(dest="mail_command")
    s = mail_sub.add_parser("send", help="Send mail", parents=[access_parent])
    s.add_argument("--to", required=True)
    s.add_argument("--subject", required=True)
    s.add_argument("--priority", default=None)
    s.add_argument("--ref", default=None)
    s.add_argument("--body", default=None, help="Mail body text")
    s.add_argument("body_pos", nargs="?", default=None, help="Mail body text (positional)")
    s.add_argument("--wake", action=argparse.BooleanOptionalAction, default=None,
                   help="Wake the target agent after delivery (default: auto — on for priority=today, off for fyi)")
    s.add_argument("--timeout", type=int, default=60, help="Wake marker lifetime in seconds")
    s.add_argument("--depth", type=int, default=1, help="Call depth (internal; >=2 suppresses wake)")
    s.add_argument("--idempotency-key", dest="idempotency_key", default=None,
                   help="v0.5.0: повтор с тем же ключом в окне 24ч возвращает исходный id (duplicate_suppressed)")
    s.add_argument("--verify", default=None,
                   help="v0.6.0 §7: verify-команда для чисел в письмо (семантика журнала)")
    s.add_argument("--verify-run", dest="verify_run", action="store_true",
                   help="v0.6.0 §7: прогнать --verify; только вместе с ней числа разблокированы (VERIFY_FAILED пишется честно)")
    s.add_argument("--numbers-ok", dest="numbers_ok", action="store_true",
                   help="v0.6.0 §7 poka-yoke bypass: сознательно отправить числа без проганной --verify")
    s.add_argument("--tags", default=None, help="Comma-separated tags for the mail")
    s.add_argument("--attach", dest="attach", action="append", nargs="+", default=None,
                   help="Attach a file by reference (path, sha256, size, mtime)")

    s = mail_sub.add_parser("read", help="Read mail by id")
    s.add_argument("id")

    s = mail_sub.add_parser("mark-read", help="Mark mail as read by id")
    s.add_argument("id")

    s = mail_sub.add_parser("thread", help="Show mail thread by root/id")
    s.add_argument("id")

    s = mail_sub.add_parser("receipt", help="Show read receipts for a mail")
    s.add_argument("id")

    s = mail_sub.add_parser("attachment", help="Show mail attachment metadata")
    s.add_argument("id")
    s.add_argument("n", type=int)

    s = mail_sub.add_parser("answer", help="Answer mail by id")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--numbers-ok", dest="numbers_ok", action="store_true",
                   help="v0.6.0 §7: сознательный обход poka-yoke для чисел в ответе")
    s.add_argument("--verify", default=None, help="v0.6.0 §7: verify-команда (как в journal)")
    s.add_argument("--verify-run", dest="verify_run", action="store_true",
                   help="v0.6.0 §7: прогнать --verify перед отправкой ответа")
    s.add_argument("--idempotency-key", dest="idempotency_key", default=None,
                   help="v1.0.0 §6 G-01: повтор с тем же ключом не создаёт второго ответа")

    s = mail_sub.add_parser("archive", help="Archive old mail")
    s.add_argument("--older-than", type=int, default=30)

    s = mail_sub.add_parser("prune", help="Prune old read mail")
    s.add_argument("--days", type=int, default=30, help="Age threshold in days")
    s.add_argument("--dry-run", action="store_true", help="Report only, do not delete")

    s = mail_sub.add_parser("list", help="List mail")
    s.add_argument("--inbox", action="store_true")
    s.add_argument("--outbox", action="store_true")
    s.add_argument("--unread", action="store_true")

    s = mail_sub.add_parser("check", help="Check unread mail without changing read state")
    s.add_argument("--include-body", action="store_true", help="Include unread message bodies for pre-LLM delivery")

    # --- v0.5.0: task objects (DESIGN-A2A-V05 §1) ---
    p = sub.add_parser("task", help="Task objects: new|claim|submit|accept|reject|block|cancel|show|list|board|link")
    task_sub = p.add_subparsers(dest="task_command")

    s = task_sub.add_parser("new", help="Create task", parents=[access_parent])
    s.add_argument("title")
    s.add_argument("--owner", default=None)
    s.add_argument("--goal", default=None)
    s.add_argument("--acceptance", action="append", default=None)
    s.add_argument("--deadline", default=None, help="YYYY-MM-DD (UTC)")
    s.add_argument("--parent", default=None)
    s.add_argument("--priority", default=None, choices=["fyi", "today", "critical"])
    s.add_argument("--acl", default=None, help="Explicit read grants (profile,@all-lex,@owner), comma-separated")
    s.add_argument("--idempotency-key", dest="idempotency_key", default=None)

    s = task_sub.add_parser("claim", help="assigned/backlog/blocked -> in_progress")
    s.add_argument("id")
    s.add_argument("--reason", default=None)
    s.add_argument("--expect-version", dest="expect_version", default=None, help="v0.6.0 §6 CAS: отвергнуть переход, если фактический version задачи не равен ожидаемому")
    s.add_argument("--as-owner", dest="as_owner", action="store_true", help="v0.6.0 §2: явный обход write-scope с аудит-событием write_scope_override")

    s = task_sub.add_parser("submit", help="in_progress -> review (attach --evidence)")
    s.add_argument("id")
    s.add_argument("--evidence", action="append", default=None, help="artifact path or journal id (repeatable)")
    s.add_argument("--expect-version", dest="expect_version", default=None, help="v0.6.0 §6 CAS: отвергнуть переход, если фактический version задачи не равен ожидаемому")
    s.add_argument("--as-owner", dest="as_owner", action="store_true", help="v0.6.0 §2: явный обход write-scope с аудит-событием write_scope_override")

    s = task_sub.add_parser("accept", help="review -> done (evidence gate)")
    s.add_argument("id")
    s.add_argument("--comment", default=None)
    s.add_argument("--evidence", action="append", default=None)
    s.add_argument("--expect-version", dest="expect_version", default=None, help="v0.6.0 §6 CAS: отвергнуть переход, если фактический version задачи не равен ожидаемому")
    s.add_argument("--no-evidence", dest="no_evidence", action="store_true",
                   help="сознательный обход гейта: done с пометкой unverified (объект + user-лента)")
    s.add_argument("--as-owner", dest="as_owner", action="store_true", help="v0.6.0 §2: явный обход write-scope с аудит-событием write_scope_override")

    s = task_sub.add_parser("reject", help="review -> rejected (reason + evidence gate)")
    s.add_argument("id")
    s.add_argument("--reason", default=None)
    s.add_argument("--evidence", action="append", default=None)
    s.add_argument("--expect-version", dest="expect_version", default=None, help="v0.6.0 §6 CAS: отвергнуть переход, если фактический version задачи не равен ожидаемому")
    s.add_argument("--no-evidence", dest="no_evidence", action="store_true")
    s.add_argument("--as-owner", dest="as_owner", action="store_true", help="v0.6.0 §2: явный обход write-scope с аудит-событием write_scope_override")

    s = task_sub.add_parser("block", help="block with reason")
    s.add_argument("id")
    s.add_argument("--reason", default=None)
    s.add_argument("--expect-version", dest="expect_version", default=None, help="v0.6.0 §6 CAS: отвергнуть переход, если фактический version задачи не равен ожидаемому")
    s.add_argument("--as-owner", dest="as_owner", action="store_true", help="v0.6.0 §2: явный обход write-scope с аудит-событием write_scope_override")

    s = task_sub.add_parser("cancel", help="cancel; children cascade to blocked:parent_cancelled")
    s.add_argument("id")
    s.add_argument("--reason", default=None)
    s.add_argument("--expect-version", dest="expect_version", default=None, help="v0.6.0 §6 CAS: отвергнуть переход, если фактический version задачи не равен ожидаемому")
    s.add_argument("--cascade", choices=["keep", "block"], default="block")
    s.add_argument("--as-owner", dest="as_owner", action="store_true", help="v0.6.0 §2: явный обход write-scope с аудит-событием write_scope_override")

    s = task_sub.add_parser("show", help="Show task (details — only participants)")
    s.add_argument("id")
    s.add_argument("--include-l1", dest="include_l1", action="store_true",
                   help="чужие L1-задачи с audit-событием (контракт v0.4.10)")

    s = task_sub.add_parser("list", help="List tasks")
    s.add_argument("--owner", default=None)
    s.add_argument("--status", default=None)
    s.add_argument("--overdue", action="store_true")
    s.add_argument("--sla", action="store_true", help="v1.0.0 §6 D-03: SLA breach (>N h overdue) + critical mail to owner")
    s.add_argument("--sla-hours", dest="sla_hours", type=int, default=0, help="Threshold hours for SLA breach")
    s.add_argument("--include-l1", dest="include_l1", action="store_true")

    s = task_sub.add_parser("board", help="Kanban-срез по профилям (markdown, user-safe)")
    s.add_argument("--include-l1", dest="include_l1", action="store_true")

    s = task_sub.add_parser("edit", help="v1.0.0 §6 J-06: edit task fields with CAS")
    s.add_argument("id")
    s.add_argument("--title", default=None)
    s.add_argument("--goal", default=None)
    s.add_argument("--owner", default=None)
    s.add_argument("--deadline", default=None)
    s.add_argument("--priority", default=None, choices=["fyi", "today", "critical"])
    s.add_argument("--acceptance", action="append", default=None)
    s.add_argument("--expect-version", dest="expect_version", default=None,
                   help="v0.6.0 §6 CAS: отвергнуть правку, если фактический version задачи не равен ожидаемому")
    s.add_argument("--as-owner", dest="as_owner", action="store_true",
                   help="v0.6.0 §2: явный обход write-scope с аудит-событием write_scope_override")

    s = task_sub.add_parser("link", help="Link task to journal/mail/artifact")
    s.add_argument("id")
    s.add_argument("--journal", action="append", default=None)
    s.add_argument("--mail", action="append", default=None)
    s.add_argument("--artifact", action="append", default=None)
    s.add_argument("--ref", action="append", default=None, help="Generic record id (journal/mail) to link")
    s.add_argument("--as-owner", dest="as_owner", action="store_true", help="v0.6.0 §2: явный обход write-scope с аудит-событием write_scope_override")

    s = task_sub.add_parser("escalate", help="Escalate overdue task(s)")
    s.add_argument("--due", dest="due", default=None, help="Task id whose deadline passed")
    s.add_argument("id", nargs="?", default=None, help="Task id (alias for --due)")
    s.add_argument("--include-l1", dest="include_l1", action="store_true")

    p = sub.add_parser("status", help="Status commands")
    status_sub = p.add_subparsers(dest="status_command")
    s = status_sub.add_parser("set", help="Set status", parents=[access_parent])
    s.add_argument("--state", default=None)
    s.add_argument("--note", default=None)
    s.add_argument("--until", default=None)
    s.add_argument("--heartbeat", dest="heartbeat", action="store_true", help="Record heartbeat ts")

    s = status_sub.add_parser("show", help="Show all statuses")

    p = sub.add_parser("dnd", help="DND commands", parents=[access_parent])
    p.add_argument("dnd_action", choices=["set", "clear", "check"])
    p.add_argument("--until", default=None)

    p = sub.add_parser("think", help="Write a thought", parents=[access_parent])
    p.add_argument("text")
    p.add_argument("--topic", default=None)

    p = sub.add_parser("ask", help="Synchronously ask another agent for an answer")
    p.add_argument("--to", required=True)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--json", action="store_true", help="Output JSON envelope instead of plain answer")
    p.add_argument("question")

    p = sub.add_parser("read", help="Read feed")
    p.add_argument("--from", dest="from_", default=None)
    p.add_argument("--unread", action="store_true")

    p = sub.add_parser("feed", help="User feed filter")
    p.add_argument("--tag", default=None)

    p = sub.add_parser("fleet", help="v1.0.0 §2 E-02: сводка статусов всех профилей")
    p.add_argument("--ttl", default=None, help="Stale status threshold (default 24h)")

    p = sub.add_parser("onboard", help="v1.0.0 §3 I-02: мастер подключения профиля")
    p.add_argument("--profile", dest="profile", default=argparse.SUPPRESS,
                   help="Profile to onboard (default: resolved caller)")

    p = sub.add_parser("cookbook", help="v1.0.0 §4 I-03: справочник сценариев")

    p = sub.add_parser("segments", help="v1.0.0 §5 J-01: segment-chain журнала по месяцам")
    p.add_argument("--seal", action="store_true", help="Запечатать неизменённый хвост")
    p.add_argument("--month", dest="month", default=None, help="Месяц YYYY-MM (default: текущий)")
    p.add_argument("-M", dest="M", default=None, help="Alias for --month")

    p = sub.add_parser("session", help="P1-8: session event-sourcing (start/event/end/wrap)")
    p.add_argument("session_action", choices=["start", "event", "end", "wrap"])
    p.add_argument("--session-id", default=None)
    p.add_argument("--type", default="note", help="event type for: start/event/end")
    p.add_argument("--data", default=None, help="payload (или stdin)")
    p.add_argument("--note", default=None)
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--cmd", default=None,
                   help="command string for wrap: a2a session wrap --session X --cmd 'echo hi'")
    p.set_defaults(func=None)

    p = sub.add_parser("replay", help="P1-8: replay/validate session JSONL")
    p.add_argument("session_file")
    p.add_argument("--check", action="store_true")
    p.set_defaults(func=None)

    p = sub.add_parser("cards", help="P1-7: Agent Cards operations")
    cs = p.add_subparsers(dest="cards_command")
    cs.add_parser("validate", help="Validate all cards against schema")

    p = sub.add_parser("who-can", help="P1-7: какие профили заявляют capability")
    p.add_argument("task")

    p = sub.add_parser("handoff", help="Create/list handoffs", parents=[access_parent])
    p.add_argument("--to", default=None)
    p.add_argument("--task", default=None)
    p.add_argument("--acceptance", default=None)
    p.add_argument("--payload", default=None)
    p.add_argument("--for", dest="list_for", default=None)
    p.add_argument("--status", default=None)

    p = sub.add_parser("lock", help="Lock commands")
    lock_sub = p.add_subparsers(dest="lock_command")
    s = lock_sub.add_parser("list", help="List locks")
    s = lock_sub.add_parser("clean", help="Remove expired locks")
    s = lock_sub.add_parser("acquire", help="Acquire scope lock")
    s.add_argument("--scope", required=True)
    s.add_argument("--ttl", required=True)
    s.add_argument("--reason", default="")
    s = lock_sub.add_parser("release", help="Release scope lock")
    s.add_argument("--scope", required=True)
    s.add_argument("--ttl", default=None)
    s = lock_sub.add_parser("set", help="Set lock", parents=[access_parent])
    s.add_argument("--target", required=True)
    s.add_argument("--reason", default="")
    s.add_argument("--expire", required=True)

    p = sub.add_parser("alert", help="Alert commands")
    alert_sub = p.add_subparsers(dest="alert_command")
    s = alert_sub.add_parser("set", help="Set alert", parents=[access_parent])
    s.add_argument("--target", required=True)
    s.add_argument("--severity", required=True)
    s.add_argument("--active-until", required=True)
    s.add_argument("body", nargs="?", default="")

    s = alert_sub.add_parser("list", help="List active alerts")

    s = alert_sub.add_parser("purge-expired", help="Purge expired alerts (dry-run default)")
    s.add_argument("--dry-run", action="store_true", help="Report only, do not move")
    s.add_argument("--yes", action="store_true", help="Move expired alerts to .state/expired")

    p = sub.add_parser("user", help="User feed commands")
    user_sub = p.add_subparsers(dest="user_command")
    s = user_sub.add_parser("post", help="Post to User feed", parents=[access_parent])
    s.add_argument("text")
    s.add_argument("--kind", default="fyi")

    s = user_sub.add_parser("rules", help="Show or set user feed redaction rules")
    s.add_argument("--show", action="store_true", help="Show current rules")
    s.add_argument("--set", dest="set_file", metavar="FILE", help="Load rules from FILE")

    s = sub.add_parser("scrub-config", help="v0.8.0 §6: per-channel redact/block rules")
    s.add_argument("--show", action="store_true")
    s.add_argument("--set", dest="set_file", default=None, metavar="JSON")

    p = sub.add_parser("secrets", help="Secrets lifecycle commands")
    secrets_sub = p.add_subparsers(dest="secrets_command")
    s = secrets_sub.add_parser("report", help="Aggregate secret_redacted audit events")
    s.add_argument("--notify", action="store_true", help="Mark .state/secrets_watch flag")
    s.add_argument("--days", type=int, default=None, help="Limit to last N days")
    s.add_argument("--profile", default=None, help="Filter by profile (caller)")

    p = sub.add_parser("quarantine", help="Quarantine suspicious payloads")
    q_sub = p.add_subparsers(dest="quarantine_command")
    q_sub.add_parser("list", help="List quarantined payloads (metadata only)")
    qs = q_sub.add_parser("show", help="Show quarantined payload (author only)")
    qs.add_argument("id")
    qp = q_sub.add_parser("purge", help="Purge quarantined payloads (dry-run default)")
    qp.add_argument("--yes", action="store_true", help="Move purged payloads to .state/quarantine_purged")
    qp.add_argument("--dry-run", action="store_true", help="Report only, do not move")

    p = sub.add_parser("search", help="Search records")
    p.add_argument("query")
    p.add_argument("--author", default=None, help="Filter by author profile")
    p.add_argument("--type", dest="type_", default=None, choices=["journal", "mail", "thought", "user", "alert", "task"], help="Filter by record type")
    p.add_argument("--kind", dest="kind", default=None, help="Filter by kind (mail|journal|task)")
    p.add_argument("--tag", dest="tag", default=None, help="Filter by tag")
    p.add_argument("--task", dest="task", default=None, help="Filter by task id referenced")
    p.add_argument("--since", default=None, help="Filter by date (YYYY-MM-DD or 7d)")

    p = sub.add_parser("index", help="Index management")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--increment", dest="increment", action="store_true")
    p.add_argument("--update", dest="update", action="store_true")
    p.add_argument("--incremental", dest="incremental", action="store_true")
    p = sub.add_parser("digest", help="Digest summary for caller")
    p.add_argument("--since", default=None, help="Since (today or date)")
    p.add_argument("--date", dest="date", default=None, help="Date D")
    p.add_argument("--mail", action="store_true", help="Send one mail to self")

    p = sub.add_parser("owner", help="Owner commands")
    owner_sub = p.add_subparsers(dest="owner_command")
    s = owner_sub.add_parser("watch", help="Owner watch delta")
    s.add_argument("--since", default=None, help="ts or file cursor")
    s.add_argument("--full", action="store_true", help="Include bodies")

    p = sub.add_parser("doctor", help="Run health check")
    p.add_argument("--artifacts", action="store_true", help="Reconcile artifact proofs (path/sha)")
    doctor_sub = p.add_subparsers(dest="doctor_command", required=False)
    dr = doctor_sub.add_parser("run", help="Run health check")
    dr.add_argument("--ttl", default=None, help="Stale status threshold (default 24h)")
    dr.add_argument("--artifacts", action="store_true", help="Reconcile artifact proofs (path/sha)")
    da = doctor_sub.add_parser("artifacts", help="Reconcile artifact proofs (path/sha)")
    da.add_argument("--ttl", default=None, help="Stale status threshold (default 24h)")
    da.add_argument("--artifacts", action="store_true", default=True, help="Reconcile artifact proofs (path/sha)")
    p = sub.add_parser("migrate", help="v0.9.0 §7: schema migration (record_version bump)")
    p.add_argument("--dry-run", action="store_true", help="Report only, do not modify")
    p.add_argument("--to", default="0.9", help="Target record_version (default 0.9)")
    p.add_argument("--yes", action="store_true", help="Apply migration (add/bump record_version)")

    p = sub.add_parser("prune", help="v0.9.0 §8: retention pruning (dry-run default)")
    p.add_argument("--older-than", dest="older_than", default="365d", help="Age threshold (e.g. 365d)")
    p.add_argument("--kind", dest="kind", default="journal", help="journal|mail|task|thoughts|alert|... or comma list")
    p.add_argument("--yes", action="store_true", help="Execute (move to .state/archive)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="Report only (default)")

    p = sub.add_parser("metrics", help="v0.9.0 §9: metrics aggregation")
    p.add_argument("--week", dest="week", default=None, help="Limit to last N weeks")

    p = sub.add_parser("export", help="v0.9.0 §10: verifiable export")
    p.add_argument("--ref", dest="ref", default=None, help="Record id to export")
    p.add_argument("--out", dest="out", default=None, help="Output file")
    p.add_argument("--verify", dest="verify", default=None, metavar="FILE", help="Verify an export file")

    p = sub.add_parser("snapshot", help="v0.6.0 §4: tamper baseline — --save writes bus_hashes.json; bare = compare")
    p.add_argument("--save", action="store_true", help="Явно сохранить baseline (единственный писатель .state/bus_hashes.json)")

    p = sub.add_parser("backup", help="v0.6.0 §5: tar.gz шины (без .state) или --restore FILE [--yes]")
    p.add_argument("--dest", default=None, help="Каталог для архива (default: <bus>/backups)")
    p.add_argument("--restore", default=None, metavar="FILE", help="Восстановить из tar.gz (по умолчанию dry-run)")
    p.add_argument("--yes", action="store_true", help="Применить restore (без флага — только отчёт о конфликтах)")

    p = sub.add_parser("verify", help="Verify integrity")
    p.add_argument("--supersede", default=None, metavar="JOURNAL_ID",
                   help="Supersede an artifact proof in the given journal record (write op)")
    p.add_argument("--artifact", default=None, metavar="PATH",
                   help="Artifact path whose proof is superseded (with --supersede)")
    p.add_argument("--reason", default=None, metavar="TEXT",
                   help="Why the old proof no longer matches (required with --supersede)")

    return parser


def dispatch(args: Namespace) -> None:
    cmd = args.command
    if cmd == "session":
        sessions_mod.cmd_session(args)
    elif cmd == "cards":
        import time as _t
        start = _t.time()
        if args.cards_command == "validate":
            okk, report = cards_mod.validate_all()
            if okk:
                util.ok({"report": report}, elapsed_ms=int((_t.time()-start)*1000))
            util.fail(util.EXIT_INVALID_RECORD, "cards_invalid", {"report": report})
        util.fail(util.EXIT_USAGE_ERROR, "unknown cards command")
    elif cmd == "who-can":
        import time as _t
        start = _t.time()
        util.ok({"matches": cards_mod.who_can(args.task)},
                elapsed_ms=int((_t.time()-start)*1000))
    elif cmd == "replay":
        sessions_mod.cmd_replay(args)
    elif cmd == "log":
        journal.cmd_log(args)
    elif cmd == "journal":
        journal.cmd_journal(args)
    elif cmd == "mail":
        mc = args.mail_command
        if mc == "send":
            body = args.body if args.body is not None else args.body_pos
            if body is None and not sys.stdin.isatty():
                body = sys.stdin.read()
            if body is None:
                body = ""
            args.body = body
            mail.cmd_mail_send(args)
        elif mc == "read":
            mail.cmd_mail_read(args)
        elif mc == "mark-read":
            mail.cmd_mail_mark_read(args)
        elif mc == "thread":
            mail.cmd_mail_thread(args)
        elif mc == "receipt":
            mail.cmd_mail_receipt(args)
        elif mc == "attachment":
            mail.cmd_mail_attachment(args)
        elif mc == "answer":
            mail.cmd_mail_answer(args)
        elif mc == "archive":
            mail.cmd_mail_archive(args)
        elif mc == "prune":
            mail.cmd_mail_prune(args)
        elif mc == "list" or mc is None:
            mail.cmd_mail_list(args)
        elif mc == "check":
            mail.cmd_mail_check(args)
        else:
            util.fail(util.EXIT_USAGE_ERROR, f"unknown mail subcommand: {mc}")
    elif cmd == "task":
        tasks_mod.cmd_task(args)
    elif cmd == "fleet":
        status.cmd_fleet(args)
    elif cmd == "onboard":
        status.cmd_onboard(args)
    elif cmd == "cookbook":
        cookbook_mod.cmd_cookbook(args)
    elif cmd == "segments":
        segments_mod.cmd_segments(args)
    elif cmd == "status":
        if args.status_command is None:
            args.state = None
            args.note = None
            args.until = None
            status.cmd_status(args)
        elif args.status_command == "set":
            status.cmd_status(args)
        elif args.status_command == "show":
            args.state = None
            status.cmd_status(args)
    elif cmd == "dnd":
        status.cmd_dnd(args)
    elif cmd == "think":
        thoughts.cmd_think(args)
    elif cmd == "ask":
        rpc.cmd_ask(args)
    elif cmd == "read":
        _cmd_read(args)
    elif cmd == "feed":
        _cmd_feed(args)
    elif cmd == "handoff":
        handoff.cmd_handoff(args)
    elif cmd == "lock":
        lc = args.lock_command
        if lc == "list" or lc is None:
            locks.cmd_lock_list(args)
        elif lc == "acquire":
            locks.cmd_lock_acquire(args)
        elif lc == "release":
            locks.cmd_lock_release(args)
        elif lc == "set":
            locks.cmd_lock_set(args)
        elif lc == "clean":
            locks.cmd_lock_clean(args)
        else:
            util.fail(util.EXIT_USAGE_ERROR, f"unknown lock subcommand: {lc}")
    elif cmd == "alert":
        ac = args.alert_command
        if ac == "set":
            alerts.cmd_alert_set(args)
        elif ac == "list" or ac is None:
            alerts.cmd_alert_list(args)
        elif ac == "purge-expired":
            alerts.cmd_alert_purge_expired(args)
        else:
            util.fail(util.EXIT_USAGE_ERROR, f"unknown alert subcommand: {ac}")
    elif cmd == "user":
        rc = args.user_command
        if rc == "post":
            userfeed.cmd_user_post(args)
        elif rc == "rules":
            userfeed.cmd_user_rules(args)
        else:
            util.fail(util.EXIT_USAGE_ERROR, f"unknown user subcommand: {rc}")
    elif cmd == "search":
        _cmd_search(args)
    elif cmd == "index":
        index.cmd_index(args)
    elif cmd == "scrub-config":
        from . import scrub_config
        if getattr(args, "set_file", None):
            util.ok(scrub_config.set_from_file(args.set_file))
        else:
            util.ok(scrub_config.show())

    elif cmd == "secrets":
        sc = getattr(args, "secrets_command", None)
        if sc == "report" or sc is None:
            secrets_mod.cmd_secrets_report(args)
        else:
            util.fail(util.EXIT_USAGE_ERROR, f"unknown secrets subcommand: {sc}")
    elif cmd == "quarantine":
        qc = getattr(args, "quarantine_command", None)
        if qc == "list" or qc is None:
            quarantine_mod.cmd_quarantine_list(args)
        elif qc == "show":
            quarantine_mod.cmd_quarantine_show(args)
        elif qc == "purge":
            quarantine_mod.cmd_quarantine_purge(args)
        else:
            util.fail(util.EXIT_USAGE_ERROR, f"unknown quarantine subcommand: {qc}")
    elif cmd == "digest":
        digest_mod.cmd_digest(args)
    elif cmd == "owner":
        oc = getattr(args, "owner_command", None)
        if oc == "watch" or oc is None:
            owner_mod.cmd_owner_watch(args)
        else:
            util.fail(util.EXIT_USAGE_ERROR, f"unknown owner subcommand: {oc}")
    elif cmd == "doctor":
        doctor.cmd_doctor(args)
    elif cmd == "snapshot":
        snapshot_mod.cmd_snapshot(args)
    elif cmd == "backup":
        backup_mod.cmd_backup(args)
    elif cmd == "prune":
        prune_mod.cmd_prune(args)
    elif cmd == "metrics":
        metrics_mod.cmd_metrics(args)
    elif cmd == "export":
        export_mod.cmd_export(args)
    elif cmd == "migrate":
        migrate_mod.cmd_migrate(args)
    elif cmd == "verify":
        _cmd_verify(args)
    else:
        util.fail(util.EXIT_USAGE_ERROR, "no_command")


def _acl_indexed_allows(path_str: str, profile: str) -> bool:
    """v0.5.0: explicit acl narrows index-based readers too.

    Records WITHOUT acl return True here — the legacy L1 contract is already
    enforced by the callers, so pre-v0.5 result sets cannot shrink.
    """
    try:
        rec = util.read_record(Path(path_str))
    except Exception:
        return True
    if not rec:
        return True
    acl = rec.get("acl")
    if not isinstance(acl, list) or not acl:
        return True
    from . import access as acl_mod
    return acl_mod.may_read(rec, profile)


def _cmd_read(args: Namespace) -> None:
    import sqlite3

    profile = util.resolve_profile(args)
    start = time.time()
    entries = []
    conn = sqlite3.connect(str(index.db_path()))
    cur = conn.cursor()
    cur.execute("SELECT id, type, author, access, created_at, path, body FROM records")
    for row in cur.fetchall():
        id_, type_, author, access, created_at, path, body = row
        if access not in schema.ACCESS_LEVELS:
            continue
        if access == "L1":
            from . import auditlog

            auditlog.log("read_l1", profile, path)
            if author != profile:
                continue
        if not _acl_indexed_allows(path, profile):
            continue
        if args.from_ and author != args.from_:
            continue
        if type_ == "mail":
            rec = util.read_record(Path(path))
            if args.unread and rec.get("msg_status") != "unread":
                continue
        entries.append({
            "id": id_,
            "type": type_,
            "author": author,
            "access": access,
            "created_at": created_at,
            "snippet": body[:200] if body else "",
        })
    conn.close()
    util.ok({"count": len(entries), "entries": entries}, elapsed_ms=int((time.time() - start) * 1000))


def _cmd_feed(args: Namespace) -> None:
    import sqlite3

    profile = util.resolve_profile(args)
    start = time.time()
    entries = []
    conn = sqlite3.connect(str(index.db_path()))
    cur = conn.cursor()
    cur.execute(
        "SELECT id, author, access, created_at, path, body FROM records WHERE type='user-note' AND (access != 'L1' OR author = ?)",
        (profile,),
    )
    for row in cur.fetchall():
        id_, author, access, created_at, path, body = row
        if access not in schema.ACCESS_LEVELS:
            continue
        rec = util.read_record(Path(path))
        if rec and not _acl_indexed_allows(path, profile):
            continue
        kind = rec.get("kind") if rec else "fyi"
        if args.tag and kind != args.tag:
            continue
        entries.append({"id": id_, "author": author, "kind": kind, "created_at": created_at, "snippet": body[:200]})
    conn.close()
    util.ok({"count": len(entries), "entries": entries}, elapsed_ms=int((time.time() - start) * 1000))


def _escape_fts_query(query: str) -> str:
    """Quote tokens that contain hyphens so FTS5 does not treat '-' as syntax."""
    tokens = query.split()
    out = []
    for tok in tokens:
        upper = tok.upper()
        if upper in ("AND", "OR", "NOT"):
            out.append(tok)
            continue
        if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
            out.append(tok)
            continue
        if "-" in tok and not tok.startswith("-"):
            out.append('"' + tok.replace('"', '""') + '"')
            continue
        out.append(tok)
    return " ".join(out)


def _parse_since(value):
    if not value:
        return None
    v = str(value).strip().lower()
    today = util.now_utc().date()
    if v == "today":
        d = today
    elif v.endswith("d"):
        try:
            n = int(v[:-1])
        except ValueError:
            util.fail(util.EXIT_USAGE_ERROR, "invalid_since_date")
        d = today - timedelta(days=n)
    else:
        try:
            d = datetime.strptime(v, "%Y-%m-%d").date()
        except Exception:
            util.fail(util.EXIT_USAGE_ERROR, "invalid_since_date")
    return datetime.combine(d, datetime.min.time()).replace(tzinfo=timezone.utc)


def _task_linked_ids(task_id: str) -> set[str]:
    """Paths/ids referenced by task.links (reverse lookup for search --task)."""
    out: set[str] = set()
    try:
        from . import tasks as _tasks_mod
        rec = _tasks_mod._load_task(task_id) if hasattr(_tasks_mod, "_load_task") else None
    except Exception:
        rec = None
    if rec is None:
        for base in (util.HOME / "tasks").glob("*.md"):
            r0 = util.read_record(base)
            if r0 and r0.get("id") == task_id:
                rec = r0
                break
    if not rec:
        return out
    links = rec.get("links") or {}
    for v in links.values():
        if isinstance(v, list):
            out.update(str(x) for x in v)
    return out


def _cmd_search(args: Namespace) -> None:
    import sqlite3

    profile = util.resolve_profile(args)
    start = time.time()
    since_dt = _parse_since(getattr(args, "since", None))

    type_map = {"user": "user-note"}
    filter_type = None
    if getattr(args, "kind", None):
        k = str(args.kind).strip().lower()
        kind_map = {"mail": "mail", "journal": "journal", "task": "task",
                    "thought": "thought", "user": "user-note", "alert": "alert"}
        filter_type = kind_map.get(k, k)
    elif getattr(args, "type_", None):
        filter_type = type_map.get(args.type_, args.type_)

    has_filters = bool(getattr(args, "author", None) or getattr(args, "type_", None)
                       or getattr(args, "kind", None) or getattr(args, "tag", None)
                       or getattr(args, "task", None) or getattr(args, "since", None))

    # Plain search keeps the historical auto-rebuild so existing flows stay green.
    # Filtered search queries the existing index without rebuilding (incremental).
    if not has_filters:
        index.rebuild()
    else:
        index.init_db()

    conn = sqlite3.connect(str(index.db_path()))
    cur = conn.cursor()
    try:
        cur.execute("SELECT path, body FROM fts WHERE fts MATCH ?", (_escape_fts_query(args.query),))
    except Exception as e:
        conn.close()
        util.fail(util.EXIT_USAGE_ERROR, f"search_failed: {e}")
    paths = [row[0] for row in cur.fetchall()]
    results = []
    for path in paths:
        cur.execute("SELECT id, type, author, access, created_at, path, body FROM records WHERE path=?", (path,))
        row = cur.fetchone()
        if not row:
            continue
        id_, type_, author, access, created_at, path, body = row
        if access not in schema.ACCESS_LEVELS:
            continue
        if args.author and author != args.author:
            continue
        if filter_type and type_ != filter_type:
            continue
        if since_dt:
            try:
                rec_created = util.parse_utc(created_at)
            except Exception:
                continue
            if rec_created.date() < since_dt.date():
                continue
        # tag filter
        tag_filter = getattr(args, "tag", None)
        if tag_filter:
            rec = util.read_record(Path(path))
            tags = rec.get("tags") if rec else None
            if not isinstance(tags, list) or tag_filter not in tags:
                continue
        # task filter (v0.8.0 §3): reverse lookup via task.links + task_id fields
        task_filter = getattr(args, "task", None)
        if task_filter:
            rec = util.read_record(Path(path))
            if not rec:
                continue
            tids = []
            if rec.get("task_id"):
                tids.append(str(rec.get("task_id")))
            if isinstance(rec.get("task_ids"), list):
                tids.extend(str(x) for x in rec.get("task_ids"))
            linked = _task_linked_ids(task_filter)
            if task_filter not in tids and not (linked and (id_ in linked or str(path) in linked)):
                continue
        if access == "L1":
            from . import auditlog

            auditlog.log("search_l1", profile, path)
            if author != profile:
                continue
        if not _acl_indexed_allows(path, profile):
            continue
        results.append({
            "id": id_,
            "type": type_,
            "author": author,
            "access": access,
            "created_at": created_at,
            "snippet": body[:200] if body else "",
        })
    conn.close()
    util.ok({"count": len(results), "results": results}, elapsed_ms=int((time.time() - start) * 1000))


def _supersede_proof(journal_id: str, artifact: str, reason: str, profile: str) -> None:
    """Append a supersede entry for one artifact proof (v0.2.5).

    Never rewrites history: the original artifact_proofs entry stays intact.
    Adds/extends `artifact_proofs_superseded` in the journal front matter with
    the freshly measured sha256 of the artifact and the stated reason.
    """
    import hashlib

    apath = Path(artifact).expanduser()
    if not apath.exists():
        util.fail(util.EXIT_USAGE_ERROR, f"artifact_not_found: {apath}")
    h = hashlib.sha256()
    with open(apath, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    new_sha = h.hexdigest()

    target = None
    for path in index._all_record_files():
        rec = util.read_record(path)
        if rec and rec.get("id") == journal_id and rec.get("type") == "journal":
            target = (path, rec)
            break
    if target is None:
        util.fail(util.EXIT_USAGE_ERROR, f"journal_record_not_found: {journal_id}")
    rpath, rec = target

    proofs = rec.get("artifact_proofs") or []
    proof = next((p_ for p_ in proofs if p_.get("path") == str(apath)), None)
    if proof is None:
        util.fail(util.EXIT_USAGE_ERROR, f"artifact_not_in_record_proofs: {apath}")
    old_sha = proof.get("sha256")
    if old_sha == new_sha:
        util.ok({"superseded": False, "reason": "proof_already_matches", "artifact": str(apath)})

    superseded = rec.get("artifact_proofs_superseded") or []
    if any(s.get("path") == str(apath) and s.get("new_sha256") == new_sha for s in superseded):
        util.ok({"superseded": False, "reason": "already_superseded_to_this_sha", "artifact": str(apath)})
    # v0.6.0 §1: канал supersede reason — redact + аудит-событие при редактуре
    clean_reason, r_hits = scrubber.redact(reason)
    if r_hits:
        from . import auditlog
        auditlog.log("secret_redacted", profile, "supersede_reason",
                     {"hits": sorted(set(r_hits)), "journal_id": journal_id})
    superseded.append({
        "path": str(apath),
        "old_sha256": old_sha,
        "new_sha256": new_sha,
        # v0.4.9 (sa-0, high): reason идёт через scrubber — раньше секрет в
        # --reason попадал в journal-запись в обход редакции.
        "reason": clean_reason,
        "superseded_by": profile,
        "superseded_at": util.utc_iso(),
    })
    rec["artifact_proofs_superseded"] = superseded
    rec.pop("_path", None)
    rec.pop("_body", None)
    text = rpath.read_text(encoding="utf-8-sig")
    meta, body = util.split_front_matter(text)
    meta["artifact_proofs_superseded"] = superseded
    util.write_atomically(rpath, util.join_front_matter(meta, body))
    util.ok({
        "superseded": True,
        "journal_id": journal_id,
        "artifact": str(apath),
        "old_sha256": old_sha,
        "new_sha256": new_sha,
        "by": profile,
    })


def _cmd_verify(args: Namespace) -> None:
    if args.supersede:
        if not args.artifact or not args.reason:
            util.fail(util.EXIT_USAGE_ERROR, "supersede_requires_artifact_and_reason")
        _supersede_proof(args.supersede, args.artifact, args.reason, util.resolve_profile(args))
        return
    import sqlite3

    start = time.time()
    issues = []
    superseded_ok = 0
    for path in index._all_record_files():
        rec = util.read_record(path)
        if not rec:
            issues.append({"path": str(path), "reason": "unparseable_front_matter"})
            continue
        if not index.access_matches_path(rec, path):
            issues.append({"path": str(path), "reason": "in_place_access_mismatch"})
            continue
        ok, reason = schema.validate(rec, rec.get("_body", ""))
        if not ok:
            issues.append({"path": str(path), "reason": reason})
        # v0.2.1 (review pass 3): сверка зафиксированных sha256 артефактов
        # v0.2.5: superseded-цепочка — свежайший supersede по пути заменяет базовый sha
        if rec.get("type") == "journal" and rec.get("artifact_proofs"):
            import hashlib
            sup_map: dict[str, str] = {}
            for s in rec.get("artifact_proofs_superseded") or []:
                sup_map[s.get("path", "")] = s.get("new_sha256", "")
            for proof in rec["artifact_proofs"]:
                apath = Path(proof.get("path", ""))
                want = sup_map.get(str(apath)) or proof.get("sha256")
                if want != proof.get("sha256") and want:
                    superseded_ok += 1
                if not proof.get("exists"):
                    issues.append({"path": str(path), "reason": f"artifact_missing: {apath}"})
                    continue
                if not apath.exists():
                    issues.append({"path": str(path), "reason": f"artifact_gone: {apath}"})
                    continue
                if not want:
                    continue
                try:
                    h = hashlib.sha256()
                    with open(apath, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            h.update(chunk)
                    if h.hexdigest() != want:
                        issues.append({
                            "path": str(path),
                            "reason": f"artifact_changed: {apath}",
                        })
                except Exception as exc:
                    issues.append({"path": str(path), "reason": f"artifact_read_error: {apath}: {exc}"})
    if issues:
        util.fail(util.EXIT_INVALID_RECORD, "verify_failed", {"issues": issues, "superseded_ok": superseded_ok}, status="error", elapsed_ms=int((time.time() - start) * 1000))
    util.ok({"verified": True, "records": len(list(index._all_record_files())), "superseded_ok": superseded_ok}, elapsed_ms=int((time.time() - start) * 1000))


def main(argv: list[str] | None = None) -> int:
    if util.HOME_CONFIGURATION_ERROR:
        util.fail(util.EXIT_USAGE_ERROR, util.HOME_CONFIGURATION_ERROR, {"path": str(util.HOME)})
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return util.EXIT_USAGE_ERROR
    try:
        dispatch(args)
        return util.EXIT_OK
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0


if __name__ == "__main__":
    sys.exit(main())
