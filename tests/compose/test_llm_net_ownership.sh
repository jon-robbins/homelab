#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

fail() { echo "FAIL: $*" >&2; exit 1; }

grep -qE '^[[:space:]]*llm_net:' compose/base.yml || fail "llm_net must be defined in compose/base.yml"
grep -qE '^[[:space:]]*internal:[[:space:]]*true' compose/base.yml || fail "llm_net must be internal in base.yml"

  [[ -f "$f" ]] || continue
  if grep -qE '^networks:' "$f"; then
    fail "$f must not declare a top-level networks: block (owned by compose/base.yml)"
  fi
done

echo "PASS: llm_net / network ownership consolidated in base.yml"
