#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

readme="README.md"
[[ -f "$readme" ]] || { echo "FAIL: missing $readme"; exit 1; }

required=(
  '^# Homelab Docker Template'
  '^## Documentation map'
  '^## Architecture at a glance'
  '^## Quick Start'
  '^## Features'
  '^## License'
)

fail=0
for pat in "${required[@]}"; do
  if ! grep -qE "$pat" "$readme"; then
    echo "FAIL: README missing heading matching /$pat/"
    fail=1
  fi
done

removed=(
  '^## Repository Structure'
  '^## CI/CD'
  '^## Configuration'
  '^## Python Workers'
  '^## Security'
)

for pat in "${removed[@]}"; do
  if grep -qE "$pat" "$readme"; then
    echo "FAIL: README still contains removed section /$pat/"
    fail=1
  fi
done

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "PASS: README structure"
