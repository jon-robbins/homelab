#!/usr/bin/env bash
# Local CI: mirrors the .github/workflows/ci.yml validate job.
# Usage: bash scripts/ci/ci-local.sh [all|compose|tests|validate|build] [--no-docker]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

PASS=0
FAIL=0
SKIP=0

run_step() {
  local name="$1"; shift
  printf "\n━━ %s\n" "$name"
  if "$@"; then
    printf "  ✓ %s\n" "$name"
    PASS=$((PASS + 1))
  else
    printf "  ✗ %s\n" "$name"
    FAIL=$((FAIL + 1))
  fi
}

run_docker_step() {
  local name="$1"; shift
  if (( NO_DOCKER )); then
    printf "SKIPPED (--no-docker): %s\n" "$name"
    SKIP=$((SKIP + 1))
  else
    run_step "$name" "$@"
  fi
}

with_ci_secret_placeholders() {
  (
    # shellcheck source=/dev/null
    source "${ROOT}/scripts/ci/export-ci-secret-placeholders.sh"
    "$@"
  )
}

SCOPE=all
SCOPE_SET=0
NO_DOCKER=0
for argument in "$@"; do
  case "$argument" in
    --no-docker) NO_DOCKER=1 ;;
    all|compose|tests|validate|build)
      if (( SCOPE_SET )); then
        echo "FATAL: Multiple scopes are not allowed." >&2
        exit 2
      fi
      SCOPE="$argument"
      SCOPE_SET=1
      ;;
    *) echo "FATAL: Scope '$argument' is invalid." >&2; exit 2 ;;
  esac
done
case "$SCOPE" in
  all|compose|tests|validate|build) ;;
  *) echo "FATAL: Scope '$SCOPE' is invalid." >&2; exit 2 ;;
esac

export SKIP_HOST_PORT_PREFLIGHT=1
export COMPOSE_ENV_FILES=/dev/null

if [[ "$SCOPE" == "all" || "$SCOPE" == "compose" || "$SCOPE" == "validate" ]]; then
  run_docker_step "Compose config with synthetic secrets" \
    bash "${ROOT}/scripts/ci/compose-config-check.sh"
fi

if [[ "$SCOPE" == "all" || "$SCOPE" == "compose" || "$SCOPE" == "tests" || "$SCOPE" == "validate" ]]; then
  run_docker_step "Compose shell tests" bash "${ROOT}/scripts/ci/run-compose-tests.sh"
fi

if [[ "$SCOPE" == "all" || "$SCOPE" == "tests" || "$SCOPE" == "validate" ]]; then
  run_step "Ops shell tests" bash "${ROOT}/tests/ops/run_tests.sh"
fi

if [[ "$SCOPE" == "all" || "$SCOPE" == "tests" || "$SCOPE" == "validate" ]]; then
  CI_TMP="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/homelab-ci-$$"
  mkdir -p "$CI_TMP"
  trap 'rm -rf "$CI_TMP"' EXIT

  WORKERS_DIR="${ROOT}/apps/homelab-workers"
  WORKERS_VENV="${CI_TMP}/workers-venv"
  MCP_VENV="${CI_TMP}/mcp-venv"
  AGENT_VENV="${CI_TMP}/homelab-agent-venv"
  MEDIA_VENV="${CI_TMP}/media-agent-venv"
  QWEN_VENV="${CI_TMP}/qwen-code-telegram-venv"

  python3 -m venv "$WORKERS_VENV"
  "$WORKERS_VENV/bin/pip" install -q -e "${WORKERS_DIR}[dev]"
  run_step "Workflow security policy" "$WORKERS_VENV/bin/python" -m pytest -q "${ROOT}/tests/ci/test_workflow_security.py"
  run_step "CI step parity" "$WORKERS_VENV/bin/python" -m pytest -q "${ROOT}/tests/ci/test_ci_step_parity.py"
  run_step "Release pytest" "$WORKERS_VENV/bin/python" -m pytest -q "${ROOT}/tests/release"
  run_step "Package pytest" "$WORKERS_VENV/bin/python" -m pytest -q "${WORKERS_DIR}/src/homelab_workers/tests"
  run_step "Package ruff" "$WORKERS_VENV/bin/ruff" check "${WORKERS_DIR}/src" "${WORKERS_DIR}/src/homelab_workers/tests"

  python3 -m venv "$MCP_VENV"
  "${MCP_VENV}/bin/pip" install -q "${WORKERS_DIR}" -e "${ROOT}/apps/homelab-mcp" pytest pytest-asyncio pytest-httpx respx
  run_step "MCP pytest" bash -c "cd '${ROOT}/apps/homelab-mcp' && '${MCP_VENV}/bin/python' -m pytest -q"

  python3 -m venv "$AGENT_VENV"
  "${AGENT_VENV}/bin/pip" install -q -e "${ROOT}/apps/homelab-agent[dev]"
  run_step "Homelab agent pytest" bash -c "cd '${ROOT}/apps/homelab-agent' && '${AGENT_VENV}/bin/python' -m pytest -W 'ignore:The anyio.abc.BlockingPortal alias is deprecated:DeprecationWarning' -q"
  run_step "Homelab agent ruff" "$AGENT_VENV/bin/ruff" check "${ROOT}/apps/homelab-agent/app" "${ROOT}/apps/homelab-agent/tests"

  python3 -m venv "$MEDIA_VENV"
  "${MEDIA_VENV}/bin/pip" install -q -e "${ROOT}/apps/media-agent[dev]"
  run_step "Media agent pytest" "${MEDIA_VENV}/bin/python" -m pytest -W "ignore:The anyio.abc.BlockingPortal alias is deprecated:DeprecationWarning" -q "${ROOT}/apps/media-agent/tests"
  run_step "Media agent ruff" "$MEDIA_VENV/bin/ruff" check "${ROOT}/apps/media-agent/app" "${ROOT}/apps/media-agent/tests"

  python3 -m venv "$QWEN_VENV"
  "${QWEN_VENV}/bin/pip" install -q pytest
  run_step "Qwen code Telegram tests" bash -c "cd '${ROOT}' && export PATH='${QWEN_VENV}/bin':\$PATH && PYTHONPATH='${ROOT}/apps/homelab-mcp/src' '${QWEN_VENV}/bin/python' -m pytest -q apps/qwen-code-telegram/tests/test_response_contract.py && bash apps/qwen-code-telegram/tests/test_entrypoint.sh && bash apps/qwen-code-telegram/tests/test_role_render.sh && bash apps/qwen-code-telegram/tests/test_system_policy.sh"
  run_docker_step "Qwen runtime regressions" bash "${ROOT}/scripts/ci/run-qwen-runtime-regressions.sh"
fi

if [[ "$SCOPE" == "all" || "$SCOPE" == "tests" || "$SCOPE" == "validate" ]]; then
  run_step "Public readiness" python3 "${ROOT}/scripts/ci/public-readiness.py"

  check_bash_syntax() {
    shopt -s globstar nullglob
    local scripts=(
      "${ROOT}"/scripts/ci/*.sh
      "${ROOT}"/scripts/ops/*.sh
      "${ROOT}"/scripts/hardening/*.sh
      "${ROOT}"/tests/compose/*.sh
      "${ROOT}"/tests/runtime/*.sh
      "${ROOT}"/tests/integration/*.sh
    )
    local script
    for script in "${scripts[@]}"; do
      [[ -f "$script" ]] && bash -n "$script" && echo "OK  ${script#${ROOT}/}"
    done
  }
  run_step "Bash syntax" check_bash_syntax
  run_step "README mermaid blocks" python3 -c '
import re
from pathlib import Path
content = Path("README.md").read_text(encoding="utf-8")
raise SystemExit(0 if len(re.findall(r"```mermaid", content)) >= 1 else 1)
'
  run_step "Documentation tests" bash "${ROOT}/tests/docs/run_tests.sh"
fi

if [[ "$SCOPE" == "build" ]]; then
  run_docker_step "Build local images" \
    with_ci_secret_placeholders docker compose -f docker-compose.yml build
fi

printf "\n══════════════════════════════════════════\n"
printf "  PASS: %d   FAIL: %d   SKIP: %d\n" "$PASS" "$FAIL" "$SKIP"
printf "══════════════════════════════════════════\n"
[[ "$FAIL" -eq 0 ]]
