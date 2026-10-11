#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fail=0
for t in "${SCRIPT_DIR}"/test_*.sh; do
  echo "=== $(basename "$t") ==="
  bash "$t" || fail=1
done
exit "$fail"
