#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

echo "==> Compose config with synthetic secrets"
bash scripts/ci/compose-config-check.sh
echo "PASS: Compose config"

echo "==> Compose checks (local mode)"
bash scripts/ci/run-compose-tests.sh --local

echo "==> Documentation checks"
bash tests/docs/run_tests.sh

echo "==> Boundary, CI, and release tests"
python3 -m pytest -q -p no:cacheprovider tests/boundary tests/ci tests/release

echo "==> Public readiness"
python3 scripts/ci/public-readiness.py

echo "Public check passed."
