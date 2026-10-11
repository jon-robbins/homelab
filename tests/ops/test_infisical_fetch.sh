#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
python3 "${ROOT}/tests/ops/test_infisical_fetch.py"
