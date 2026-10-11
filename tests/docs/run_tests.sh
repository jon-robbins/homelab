#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
for t in "$DIR"/test_*.sh; do
  echo "==> $(basename "$t")"
  bash "$t"
done
echo "All doc tests passed."
