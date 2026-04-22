#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
WORKFLOW_PATH="${REPO_ROOT}/.github/workflows/ci.yml"
EVENT_NAME="${1:-pull_request}"
JOB_NAME="${2:-verify}"

if ! command -v act >/dev/null 2>&1; then
  echo "[gh-actions-local] error: act not found. Install from https://nektosact.com/"
  exit 1
fi

if [[ ! -f "${WORKFLOW_PATH}" ]]; then
  echo "[gh-actions-local] error: workflow not found at ${WORKFLOW_PATH}"
  exit 1
fi

DOCKER_HOST_VALUE="$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)"
if [[ -n "${DOCKER_HOST_VALUE}" ]]; then
  export DOCKER_HOST="${DOCKER_HOST_VALUE}"
fi

cd "${REPO_ROOT}"

echo "[gh-actions-local] workflow=${WORKFLOW_PATH}"
echo "[gh-actions-local] event=${EVENT_NAME} job=${JOB_NAME}"
if [[ -n "${DOCKER_HOST:-}" ]]; then
  echo "[gh-actions-local] using DOCKER_HOST=${DOCKER_HOST}"
fi

act "${EVENT_NAME}" \
  --workflows "${WORKFLOW_PATH}" \
  --job "${JOB_NAME}" \
  --container-architecture linux/amd64 \
  --pull=false
