#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

fail=0
for path in compose/*.yml; do
  f="${path##*/}"
  if ! grep -R -q --include='*.md' -F "$f" README.md docs; then
    echo "FAIL: kept docs missing reference to $path"
    fail=1
  fi
done
if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "PASS: README compose includes"
