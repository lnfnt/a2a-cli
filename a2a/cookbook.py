"""v1.0.0 §4 I-03: справочник сценариев (cookbook)."""
from __future__ import annotations

import time

from . import util


RECIPES = [
    {
        "id": "mail-answer-wave",
        "name": "Почта ответ-волной",
        "title": "Почта ответ-волной",
        "summary": "Отправить письмо, проверить входящие, ответить, посмотреть тред.",
        "commands": [
            'a2a mail send --to other --subject "задача" --priority today --body "описание"',
            "a2a mail check --include-body",
            "a2a mail read <id>",
            'a2a mail answer <id> "принято" --verify-run --verify "true"',
            "a2a mail thread <id>",
            "a2a mail receipt <id>",
        ],
    },
    {
        "id": "task-with-acceptance",
        "name": "Задача с приёмкой",
        "title": "Задача с приёмкой",
        "summary": "Создать задачу, взять в работу, сдать, принять.",
        "commands": [
            'a2a task new "название" --owner tester --acceptance "критерий" --deadline 2026-12-31 --priority today',
            "a2a task claim <id>",
            "a2a task submit <id> --evidence /tmp/artifact.txt",
            'a2a task accept <id> --comment "ok"',
            "a2a task show <id>",
            "a2a task list --status review",
        ],
    },
    {
        "id": "backup-snapshot-regimen",
        "name": "Бэкап+snapshot регламент",
        "title": "Бэкап+snapshot регламент",
        "summary": "Сохранить baseline, сделать бэкап, восстановить (dry-run), проверить.",
        "commands": [
            "a2a snapshot --save",
            "a2a backup --dest /tmp/a2a-backup",
            "a2a backup --restore /tmp/a2a-backup/a2a-bus-2026-01-01.tar.gz",
            "a2a doctor --artifacts",
            "a2a verify",
        ],
    },
    {
        "id": "dnd-policy",
        "name": "DND-политика",
        "title": "DND-политика",
        "summary": "Включить DND, проверить, снять.",
        "commands": [
            "a2a dnd set --until 23:59",
            "a2a status set --state dnd --until 08:00",
            "a2a dnd check",
            "a2a dnd clear",
        ],
    },
    {
        "id": "digest-morning",
        "name": "Digest утро",
        "title": "Digest утро",
        "summary": "Утренняя сводка и отправка себе.",
        "commands": [
            "a2a digest",
            "a2a digest --since today --mail",
            "a2a digest --date 2026-01-01",
        ],
    },
    {
        "id": "thread-triage",
        "name": "Тред-разбор",
        "title": "Тред-разбор",
        "summary": "Разобрать тред и найти связанные письма.",
        "commands": [
            "a2a mail thread <id>",
            "a2a mail list --inbox --unread",
            'a2a search "thread" --kind mail',
        ],
    },
    {
        "id": "secret-quarantine",
        "name": "Карантин секретов",
        "title": "Карантин секретов",
        "summary": "Отчёт по секретам, карантин, маски.",
        "commands": [
            "a2a secrets report --days 7",
            "a2a secrets report --notify",
            "a2a quarantine list",
            "a2a scrub-config --show",
        ],
    },
    {
        "id": "reconciliation",
        "name": "Reconciliation",
        "title": "Reconciliation",
        "summary": "Миграция, pruning, метрики, экспорт, doctor.",
        "commands": [
            "a2a migrate --dry-run",
            "a2a prune --older-than 365d --kind journal --dry-run",
            "a2a metrics --week 4",
            "a2a export --ref <id> --out /tmp/export.json",
            "a2a export --verify /tmp/export.json",
            "a2a doctor",
        ],
    },
]


def cmd_cookbook(args) -> None:
    start = time.time()
    # normalize: each recipe carries aliases for steps/lines
    recipes = []
    for r in RECIPES:
        rr = dict(r)
        rr["steps"] = list(r["commands"])
        rr["lines"] = list(r["commands"])
        rr["command_list"] = list(r["commands"])
        recipes.append(rr)
    all_commands = [c for r in recipes for c in r["commands"]]
    text_lines = []
    for r in recipes:
        text_lines.append(f"## {r['name']}")
        for c in r["commands"]:
            text_lines.append(c)
        text_lines.append("")
    util.ok(
        {
            "count": len(recipes),
            "recipes": recipes,
            "items": recipes,
            "entries": recipes,
            "cookbook": recipes,
            "commands": all_commands,
            "all_commands": all_commands,
            "text": "\n".join(text_lines),
        },
        elapsed_ms=int((time.time() - start) * 1000),
    )
