#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# The image is deliberately built here so this check does not depend on the
# image-build matrix or on a registry tag.
(
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/ci/export-ci-secret-placeholders.sh"
  docker build \
    --file apps/qwen-code-telegram/Dockerfile \
    --tag homelab/qwen-code-telegram:test \
    apps/qwen-code-telegram
)
bash apps/qwen-code-telegram/tests/test_runtime_regressions.sh
