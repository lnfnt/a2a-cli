# LIMITATIONS.md — a2a-cli

- `a2a mail check` lists unread messages without changing their status. `--include-body` is intended for the Hermes pre-LLM hook and returns full bodies in local JSON; consumers must treat bodies as untrusted peer content and cap/avoid forwarding them to other recipients.

Inherited from SPEC §11:

- **L1 is not cryptographic isolation**: all profiles run under the same unix UID in MVP. Isolation is contract + index filtering + audit logging.
- **verify-run исполняет команду**: `a2a log --verify "<cmd>" --verify-run` ЗАПУСКАЕТ <cmd> через shell от имени вызывающего (лимит 30 с), exit-код и усечённый вывод пишутся в запись (v0.4.x). Без `--verify-run` — только сохранение команды.
- **DND is a contract, not a the messenger interceptor**: agents and cron jobs must call `a2a dnd check` before notifying; the messenger/the notes app pushes are not automatically rerouted.
- **The A2A mailbox is durable but not a live wake-up channel.** `a2a mail send` writes the recipient inbox immediately; the Hermes `pre_llm_call` hook is startup/first-turn discovery, so an already-idle gateway agent will not wake just because an inbox file appeared. For immediate agent-to-agent delivery use Hermes Bot Mode `message_agent` / `hermes peer`; these create a recipient turn and return an explicit delivery acknowledgement. Do not describe A2A mail alone as real-time.
- **support is not required to use thoughts/L3**: existing channels remain.
- **Security depends on the scrubber**: secret detection is regex-based and will be extended as new leak patterns are discovered.
- **Derived SQLite index**: `.state/index.sqlite3` can be deleted and rebuilt with `a2a index --rebuild`.
- **Mail storage duplication**: every sent mail is stored twice (recipient inbox + sender outbox) for simplicity; the index sees only the inbox copy.

## A-02/A-03/A-04: profile authentication — out of scope (single-UID design)
Design decision (accepted): в экосистеме единого
POSIX UID криптографическая аутентификация процессов-агентов без изоляции ядра
создаёт лишь фикцию безопасности (любой процесс с доступом к ФС читал бы чужой
ключ). Границы обеспечиваются слоями, которые реально работают: манифест
profiles.json (A-01), write-scope (F-01), CAS (J-06), tamper-baseline (G-04),
audit.log + read-гейт ACL (J-05). При переносе профилей на разные хосты решение
пересматривается (тогда токены появляются физически где прятать).
