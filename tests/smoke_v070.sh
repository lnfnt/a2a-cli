#!/bin/bash
# v0.7.0 приёмочный smoke на tmp-шине (переносимый контроль; НЕ трогать живую шину)
set -u
T=$(mktemp -d); mkdir -p $T/{mail,journal,tasks,alerts,.state}
A() { A2A_HOME=$T a2a --profile coordinator "$@"; }
O() { A2A_HOME=$T a2a --profile "$1" "${@:2}"; }
F() { grep -qo "$2" <<<"$3" && echo "OK: $1" || { echo "FAIL: $1"; RC=1; }; }
RC=0

# lock cycle
F "lock acquire" '"status": "ok"' "$(A lock acquire --scope mail --ttl 60 2>&1)"
F "foreign write locked" 'locked_by_other' "$(O user-profile mail send --to colleague --subject t --body b 2>&1)"
F "owner write allowed" '"status": "ok"' "$(A mail send --to colleague --subject t --body b 2>&1)"
A lock release --scope mail >/dev/null 2>&1
F "foreign write after release" '"status": "ok"' "$(O user-profile mail send --to colleague --subject t2 --body b2 2>&1)"

# CAS + write-scope + hint
R=$(A task new "smoke-task" --owner coordinator --goal g 2>&1); ID=$(sed -E 's/.*"id": "([a-z0-9]+)".*/\1/' <<<"$R" | tail -1)
F "cas stale_version" 'stale_version' "$(A task claim $ID --expect-version 99 2>&1)"
F "non-participant rejected" 'invalid_actor' "$(O support task claim $ID 2>&1)"
F "helping error hint" '"hint"' "$(A task claim deadbeefdeadbeefdeadbeef 2>&1)"

# feed redactor
F "feed rules show" '"rules"' "$(A user rules --show 2>&1)"
A user post "телефон +7 916 123-45-67 id synthetic-id" >/dev/null 2>&1
TXT=$(cat "$(ls -t $T/user-feed/*.md | head -1)")
F "phone redacted" 'REDACTED' "$TXT"
[[ "$TXT" == *"+7 916 123-45-67"* ]] && { echo FAIL: phone-leak; RC=1; } || echo "OK: no phone leak"

# secrets report + tamper baseline
F "secrets report" '"total_events"' "$(A secrets report 2>&1)"
F "snapshot save" '"status": "ok"' "$(A snapshot --save 2>&1)"
rm -rf $T
echo "SMOKE_RC=$RC"
exit $RC
