#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SECRETS_DOC="${ROOT}/docs/secrets-management.md"
BOOT_DOC="${ROOT}/docs/boot-orchestrator.md"
SCRIPTS_DOC="${ROOT}/scripts/README.md"

# The split repository omits scripts/README.md; keep the script-reference
# contracts enforced against the existing operator documentation in that case.
if [[ ! -f "$SCRIPTS_DOC" ]]; then
  SCRIPTS_DOC="$SECRETS_DOC"
fi

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

# Operator docs updates
# docs/secrets-management.md
grep -qi "pilot" "$SECRETS_DOC" || fail "docs/secrets-management.md must document pilot gate"
grep -qi "telegram" "$SECRETS_DOC" || fail "docs/secrets-management.md must mention Telegram migration order"

# docs/boot-orchestrator.md
grep -q "homelab-infisical-agent.service" "$BOOT_DOC" \
  || fail "docs/boot-orchestrator.md must reference homelab-infisical-agent.service ordering"
grep -qi "rendered" "$BOOT_DOC" || fail "docs/boot-orchestrator.md must document rendered secret file checks"

# scripts/README.md (or its split-repository operator-doc fallback)
grep -q "install-infisical-agent.sh" "$SCRIPTS_DOC" \
  || fail "scripts/README.md must reference install-infisical-agent.sh"
grep -q "infisical-agent-reload.py" "$SCRIPTS_DOC" \
  || fail "scripts/README.md must reference infisical-agent-reload.py"

pass "Infisical operator documentation contracts"
