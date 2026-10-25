#!/bin/bash
# v0.8.0 приёмочный smoke на tmp-шине
set -u
T=$(mktemp -d); mkdir -p $T/{mail,journal,tasks,alerts,.state}
A() { A2A_HOME=$T a2a --profile coordinator "$@"; }
O() { A2A_HOME=$T a2a --profile "$1" "${@:2}"; }
F() { grep -qF "$2" <<<"$3" && echo "OK: $1" || { echo "FAIL: $1"; RC=1; }; }
RC=0

# §1 треды
R1=$(A mail send --to user-profile --subject "корень" --body b1 2>&1); ID=$(sed -E 's/.*"id": "([a-z0-9]+)".*/\1/' <<<"$R1" | tail -1)
R2=$(O user-profile mail send --to coordinator --subject otvet-odin --body b2 --ref $ID 2>&1); ID2=$(sed -E 's/.*"id": "([a-z0-9]+)".*/\1/' <<<"$R2" | tail -1)
O user-profile mail send --to coordinator --subject otvet-dva --body b3 --ref $ID2 >/dev/null 2>&1
F "thread содержит цепочку" '"thread": "' "$(A mail thread $ID 2>&1)"
F "thread включает реплику" 'otvet-dva' "$(A mail thread $ID 2>&1)"

# §2 digest
F "digest пустой ок" '"unread_inbox": []' "$(A2A_HOME=$T a2a --profile agent-profile digest 2>&1)"
F "digest видит непрочитанное" "$ID2" "$(A digest 2>&1)"

# §3 теги/поиск
A log "запись про метки" --numbers-ok --status ok --tags "дом,проверка" >/dev/null
A index --increment >/dev/null
F "поиск по тегу" 'метки' "$(A search метки --tag дом 2>&1)"

# §4 вложения
echo payload > $T/obj.txt
R=$(A mail send --to user-profile --subject вложение --body b --attach $T/obj.txt 2>&1); MID=$(sed -E 's/.*"id": "([a-z0-9]+)".*/\1/' <<<"$R" | tail -1)
F "attach sha256 метаданные" 'sha256' "$(A mail attachment $MID 1 2>&1)"
echo CHANGED > $T/obj.txt
F "attach drift" 'MOVED' "$(A2A_HOME=$T a2a --profile user-profile mail attachment $MID 1 2>&1)"

# §5 receipts
A read --inbox --profile user-profile >/dev/null 2>&1 || true
F "receipt команда жива" '"status"' "$(A mail receipt $ID 2>&1)"

# §6 scrub-config
printf '{"mail": {"kv_secret": "block"}}' > $T/cfg.json
A scrub-config --set $T/cfg.json >/dev/null
F "block отбивает" 'scrub_blocked' "$(A mail send --to user-profile --subject s --body 'password=hunter12345' 2>&1)"
F "обычный текст проходит" '"status": "ok"' "$(A mail send --to user-profile --subject s --body 'обычное тело' 2>&1)"
rm -rf $T
echo "SMOKE_RC=$RC"
exit $RC
