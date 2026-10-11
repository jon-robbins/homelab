#!/usr/bin/env bash
# Static checks: CI placeholders must not leak into live e2e/nightly mutations.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CI_LOCAL="${ROOT}/scripts/ci/ci-local.sh"

fail() { echo "FAIL: $1"; exit 1; }
pass() { echo "PASS: $1"; }

[[ -f "${CI_LOCAL}" ]] || fail "missing ci-local.sh"

grep -q 'with_ci_secret_placeholders()' "${CI_LOCAL}" \
  || fail "ci-local must define with_ci_secret_placeholders"
grep -q 'export-ci-secret-placeholders.sh' "${CI_LOCAL}" \
  || fail "placeholders must come from export-ci-secret-placeholders.sh"

# Must not globally export placeholders at script startup (pre-scope).
# A source/call at top level (not inside a function body) is forbidden.
if awk '
  /^[a-zA-Z_][a-zA-Z0-9_]*\(\)/ { in_fn=1; next }
  /^}/ { in_fn=0; next }
  !in_fn && /export-ci-secret-placeholders\.sh/ { found=1 }
  END { exit found ? 0 : 1 }
' "${CI_LOCAL}"; then
  fail "ci-local must not source export-ci-secret-placeholders.sh at top level"
fi

[[ -x "${ROOT}/scripts/ops/homelab-compose.py" ]] \
  || fail "homelab-compose.py must be executable as the canonical entrypoint"
grep -q 'homelab-compose.py" up -d --remove-orphans' "${CI_LOCAL}" \
  || fail "e2e/nightly mutation must use homelab-compose.py"
if grep -q 'scripts/ops/homelab-compose.sh' "${CI_LOCAL}"; then
  fail "ci-local must consistently use the canonical homelab-compose.py entrypoint"
fi
grep -q 'refusing to mutate with placeholders' "${CI_LOCAL}" \
  || fail "live modes must refuse when Infisical bootstrap is missing"

pass "ci-local placeholder scoping"

bash "${ROOT}/tests/ops/test_ci_quarantine.sh" || fail "test_ci_quarantine.sh failed"
