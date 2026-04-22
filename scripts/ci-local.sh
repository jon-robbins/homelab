#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PKG_DIR="${REPO_ROOT}/src/homelab_workers"
PKG_VENV="${PKG_DIR}/.venv"

declare -i TOTAL=0
declare -i PASSED=0
declare -i FAILED=0

log() {
  printf '[ci-local] %s\n' "$*"
}

run_step() {
  local name="$1"
  shift
  ((TOTAL += 1))
  log "START ${name}"
  if "$@"; then
    ((PASSED += 1))
    log "PASS  ${name}"
  else
    ((FAILED += 1))
    log "FAIL  ${name}"
  fi
}

ensure_pkg_venv() {
  if [[ -x "${PKG_VENV}/bin/python" ]]; then
    return 0
  fi

  log "Create package venv at ${PKG_VENV}"
  python3 -m venv "${PKG_VENV}"
  "${PKG_VENV}/bin/pip" install -e "${PKG_DIR}[dev]"
}

check_compose_configs() {
  docker compose -f "${REPO_ROOT}/docker-compose.network.yml" config --quiet
  docker compose -f "${REPO_ROOT}/docker-compose.media.yml" config --quiet
  docker compose -f "${REPO_ROOT}/docker-compose.llm.yml" config --quiet
}

check_optional_gpu_overlay() {
  local gpu_root="${REPO_ROOT}/docker-compose.gpu.yml"
  local gpu_template="${REPO_ROOT}/config/gpu/docker-compose.gpu.yml"
  if [[ -f "${gpu_root}" ]]; then
    docker compose \
      -f "${REPO_ROOT}/docker-compose.network.yml" \
      -f "${REPO_ROOT}/docker-compose.media.yml" \
      -f "${REPO_ROOT}/docker-compose.llm.yml" \
      -f "${gpu_root}" \
      config --quiet
    return 0
  fi

  if [[ -f "${gpu_template}" ]]; then
    docker compose \
      -f "${REPO_ROOT}/docker-compose.network.yml" \
      -f "${REPO_ROOT}/docker-compose.media.yml" \
      -f "${REPO_ROOT}/docker-compose.llm.yml" \
      -f "${gpu_template}" \
      config --quiet
  fi
}

check_bash_syntax() {
  local files=(
    "${REPO_ROOT}/scripts/setup.sh"
    "${REPO_ROOT}/tests/compose/run_tests.sh"
    "${REPO_ROOT}/tests/runtime/run_tests.sh"
  )
  local script
  for script in "${files[@]}"; do
    [[ -f "${script}" ]] && bash -n "${script}"
  done
}

run_runtime_smoke_tests() {
  if [[ "${CI_LOCAL_RUNTIME_SMOKE:-0}" != "1" ]]; then
    log "SKIP runtime smoke tests (set CI_LOCAL_RUNTIME_SMOKE=1 to enable)"
    return 0
  fi

  bash "${REPO_ROOT}/tests/runtime/run_tests.sh"
}

check_mermaid_blocks() {
  python3 - <<'PY'
from pathlib import Path
import re
import sys

readme = Path("README.md")
if not readme.exists():
    print("README.md missing", file=sys.stderr)
    sys.exit(1)

content = readme.read_text(encoding="utf-8")
count = len(re.findall(r"```mermaid", content))
if count < 1:
    print("README must contain at least one mermaid block", file=sys.stderr)
    sys.exit(1)
PY
}

main() {
  cd "${REPO_ROOT}"

  run_step "compose-shell-tests" bash "${REPO_ROOT}/tests/compose/run_tests.sh"
  run_step "compose-config" check_compose_configs
  run_step "compose-gpu-overlay" check_optional_gpu_overlay
  ensure_pkg_venv
  run_step "scripts-pytest" "${PKG_VENV}/bin/python" -m pytest -q "${REPO_ROOT}/scripts/tests"
  run_step "scripts-ruff" "${PKG_VENV}/bin/ruff" check "${REPO_ROOT}/scripts/workers"
  run_step "package-pytest" "${PKG_VENV}/bin/pytest" -q "${PKG_DIR}"
  run_step "package-ruff" "${PKG_VENV}/bin/ruff" check "${PKG_DIR}/src"
  run_step "bash-syntax" check_bash_syntax
  run_step "docs-mermaid" check_mermaid_blocks
  run_step "runtime-smoke" run_runtime_smoke_tests

  log "DONE total=${TOTAL} passed=${PASSED} failed=${FAILED}"
  if ((FAILED > 0)); then
    exit 1
  fi
}

main "$@"
