#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PATTERN='python3 -m py'"test"

if rg -n --glob '!*test_no_bare_pytest.sh' --fixed-strings "$PATTERN" "$ROOT/tests/ops"; then
  echo "FAIL: tests/ops scripts must not invoke the bare system Python pytest" >&2
  exit 1
fi

echo "PASS: tests/ops scripts use managed pytest environments"
