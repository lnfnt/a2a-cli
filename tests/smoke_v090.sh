#!/bin/bash
# v0.9.0 приёмочный smoke на tmp-шине
set -u
T=$(mktemp -d); mkdir -p $T/{mail,journal,tasks,alerts,.state}
A() { A2A_HOME=$T a2a --profile coordinator "$@"; }
O() { A2A_HOME=$T a2a --profile "$1" "${@:2}"; }
F() { grep -qF "$2" <<<"$3" && echo "OK: $1" || { echo "FAIL: $1"; RC=1; }; }
RC=0

# §1 escalation
PAST=$(date -u -d '3 days ago' +%Y-%m-%d)
R=$(A task new "overdue" --owner user-profile --deadline $PAST 2>&1); TID=$(sed -E 's/.*"id": "([a-z0-9]+)".*/\1/' <<<"$R" | tail -1)
O user-profile task escalate --due $TID >/dev/null 2>&1
F "escalation создаёт письмо" 'overdue' "$(cat $T/mail/user-profile/inbox/*.md 2>/dev/null)"
F "escalation critical" 'critical' "$(O user-profile task escalate --due $TID 2>&1)"

# §3 owner watch
F "owner watch baseline" 'owner_cursor.json' "$(A owner watch 2>&1; ls $T/.state/owner_cursor.json)"
A log "hello" >/dev/null 2>&1
F "owner watch новые записи" '"count"' "$(A owner watch 2>&1)"

# §7 migrate
mkdir -p $T/journal/2025/01
cat > $T/journal/2025/01/2025-01-01_tester_oldrec.md <<'EOF'
---
type: journal
id: oldrec
author: tester
access: L2
created_at: 2025-01-01T00:00:00Z
what_done: old entry
status: ok
artifacts: []
---
body
EOF
F "migrate dry-run детектит" 'oldrec' "$(A migrate --dry-run 2>&1)"
A migrate --yes >/dev/null 2>&1
F "migrate добавляет version" 'record_version' "$(cat $T/journal/2025/01/2025-01-01_tester_oldrec.md)"

# §8 prune dry-run
mkdir -p $T/journal/2025/01
cat > $T/journal/2025/01/2025-01-01_tester_oldjournal.md <<'EOF'
---
type: journal
id: oldjournal
author: tester
access: L2
created_at: 2025-01-01T00:00:00Z
what_done: old entry
status: ok
artifacts: []
---
body
EOF
F "prune dry-run" 'dry_run' "$(A prune --older-than 365d --kind journal 2>&1)"
F "prune dry-run список" 'oldjournal' "$(A prune --older-than 365d --kind journal 2>&1)"

# §9 metrics
F "metrics команда" 'mail' "$(A metrics 2>&1)"

# §10 export
R=$(A log "export me" 2>&1); JID=$(sed -E 's/.*"id": "([a-z0-9]+)".*/\1/' <<<"$R" | tail -1)
A export --ref $JID --out $T/export.json >/dev/null 2>&1
F "export verify" 'verified' "$(A export --verify $T/export.json 2>&1)"

# §11 dnd policy missing
F "dnd policy missing" 'dnd_policy_missing' "$(A doctor run 2>&1)"

# §6 quarantine list
F "quarantine list" 'entries' "$(A quarantine list 2>&1)"

rm -rf $T
echo "SMOKE_RC=$RC"
exit $RC
