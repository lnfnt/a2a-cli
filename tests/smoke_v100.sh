#!/bin/bash
# v1.0.0 приёмочный smoke на tmp-шине
set -u
T=$(mktemp -d); mkdir -p $T/{mail,journal,tasks,alerts,.state}
A() { A2A_HOME=$T a2a --profile coordinator "$@"; }
O() { A2A_HOME=$T a2a --profile "$1" "${@:2}"; }
F() { grep -qF "$2" <<<"$3" && echo "OK: $1" || { echo "FAIL: $1"; RC=1; }; }
RC=0

# §1 private journal: скрыт от чужого поиска
A log "личная заметка о приватном" --numbers-ok --status ok --private >/dev/null 2>&1
A index --increment >/dev/null 2>&1
F "private не светится чужому профилю" '"count": 0' "$(O user-profile search приватном 2>&1)"
F "владелец свою видит" 'приватном' "$(A search приватном 2>&1)"

# §2 fleet
F "fleet одним конвертом" '"profiles"' "$(A fleet 2>&1)"

# §3 onboard нового профиля
T2=$(mktemp -d); mkdir -p $T2/{mail,journal,tasks,alerts,.state}
A2A_HOME=$T2 a2a --profile newcomer onboard >/dev/null 2>&1
F "onboard идемпотентен (повтор ok)" '"status"' "$(A2A_HOME=$T2 a2a --profile newcomer onboard 2>&1)"

# §4 cookbook: рецепты парсятся
F "cookbook отвечает" 'recipes' "$(A cookbook 2>&1)"

# §5 segments chain seal/tamper
A log "сегментный тест 1" --numbers-ok --status ok >/dev/null 2>&1
F "segments seal" '"status": "ok"' "$(A segments --seal --month 2026-10 2>&1)"
F "seal повтор" '"status": "ok"' "$(A segments --seal --month 2026-10 2>&1)"

# §6 answer idempotency replay
R=$(O user-profile mail send --to coordinator --subject q --body "вопрос" 2>&1); QID=$(sed -E 's/.*"id": "([a-z0-9]+)".*/\1/' <<<"$R" | tail -1)
A mail answer $QID "чистый ответ" --idempotency-key A1 >/dev/null 2>&1
F "answer-дедуп реплей" 'duplicate_window' "$(A mail answer $QID "чистый ответ" --idempotency-key A1 2>&1)"

# §6 task edit CAS
TID=$(A task new "cas-edit" --owner coordinator --goal g 2>&1 | sed -E 's/.*"id": "([a-z0-9]+)".*/\1/' | tail -1)
F "edit stale_version" 'stale_version' "$(A task edit $TID --expect-version 99 --goal new 2>&1 | grep -o stale_version)"

rm -rf $T $T2
echo "SMOKE_RC=$RC"
exit $RC
