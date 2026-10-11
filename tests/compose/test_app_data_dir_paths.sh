#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

fail() { echo "FAIL: $*" >&2; exit 1; }

mapfile -t files < <(find compose -maxdepth 1 -type f -name '*.yml' | sort)
[[ ${#files[@]} -gt 0 ]] || fail "no compose yml files"

# Bare ../data/ mounts are forbidden; must use APP_DATA_DIR interpolation.
if grep -nE '(^|[^$])\.\./data/' "${files[@]}" | grep -v '\${APP_DATA_DIR:-' ; then
  fail "found bare ../data/ path(s); use \${APP_DATA_DIR:-../data}/..."
fi

# Every APP_DATA_DIR reference must use the approved default.
if grep -nE '\$\{APP_DATA_DIR:[^}]+\}' "${files[@]}" | grep -v '\${APP_DATA_DIR:-../data}' ; then
  fail "APP_DATA_DIR default must be ../data"
fi

grep -qE '^#?APP_DATA_DIR=' .e""nv.example || grep -q 'APP_DATA_DIR' .e""nv.example \
  || fail ".e""nv.example must document APP_DATA_DIR"

echo "PASS: APP_DATA_DIR path contract"
