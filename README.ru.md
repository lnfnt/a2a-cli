# a2a-cli

![license](https://img.shields.io/badge/license-Apache--2.0-blue) ![python](https://img.shields.io/badge/python-3.10%2B-green) ![release](https://img.shields.io/badge/release-v0.1.0-informational) ![tests](https://img.shields.io/badge/tests-405-brightgreen)


<p align="center"><img src="assets/poster.jpg" width="820" alt="a2a-cli poster"></p>

**Русский** · [<img src="assets/flags/gb.svg" height="16" align="top" alt="EN"> English](README.md)

Файловая шина «агент-агент»: почта, журнал, общие мысли, доска задач, блокировки, алерты,
передачи, присутствие (status/DND), синхронный RPC. Записи — Markdown с YAML front matter на
общей POSIX-файловой системе; каждая команда — один JSON-конверт. Без демона, брокера и сети.

## Установка

> Пакет `a2a-cli` на PyPI — посторонний проект. Установка только из этого репозитория.

```bash
pip install git+https://github.com/lnfnt/a2a-cli.git
a2a --help
```

Python >= 3.10, PyYAML. Из checkout: `python -m venv .venv && .venv/bin/pip install -e .`

## Быстрый старт

Полный сценарий на два профиля — в основном README (Quickstart); команды идентичны.
Один JSON-конверт на команду; коды выхода: 0 ok, 1 error, 2 degraded, 3 invalid record,
4 usage, 124 timeout; отказы содержат `hint` + `example`.

## Модель хранения

Одно дерево каталогов, append-only. Хранилище `~/.a2a`, переопределение `A2A_HOME`.
Профиль: `--profile` > `A2A_PROFILE` > `HERMES_PROFILE` > имя `HERMES_HOME` >
`~/.hermes/active_profile` > `default`. Доступ на запись: `L1` приватно, `L2` команда,
`L3` публично; fail-closed.

## Целостность

- Скраббер: шаблоны секретов редактируются или отклоняются с `secret_detected`, карантин +
  `a2a secrets report`.
- Детектор подмены: `a2a snapshot --save` (sha256) + сверка в `a2a doctor`.
- Атомарные записи, транзакции пакетов, восстановление через doctor.
- Блокировки, DND, дедупликация, audit-лог (действия, не содержимое).
- Индекс SQLite FTS5, пересобираемый.

## Ограничения

- Модель доверия single-UID: доступ к записи в каталог = возможность писать от своего
  профиля; подделка записей детектируется, подмена личности — нет.
- Сотни агентов или несколько машин — нужен настоящий брокер.
- `ask` — синхронная координация с таймаутом, не чат.

## Документация и тесты

`SPEC.md`, `SCHEMA.md`, `LIMITATIONS.md`, `NOTES.md`, `a2a cookbook`. 405 тестов на временных
`A2A_HOME`. Apache-2.0. Проверка артефактов: `sha256sum -c SHA256SUMS`, `gpg --verify *.asc`.

Автор и правообладатель: [lnfnt](mailto:lnfnt@proton.me).
