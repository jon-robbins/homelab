#!/usr/bin/env bash
# Static checks: the local gate has no live mutation modes.
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

if grep -Eq '\be2e\b|\bnightly\b' "${CI_LOCAL}"; then
  fail "retired e2e/nightly modes must not appear in ci-local.sh"
fi

pass "ci-local placeholder scoping"
