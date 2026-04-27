#!/usr/bin/env bash
# ── Local CI: mirrors .github/workflows/ci.yml + nightly E2E ────────────────
# Usage:  bash scripts/ci-local.sh          (run all CI checks — no E2E)
#         bash scripts/ci-local.sh compose   (compose config + shell tests)
#         bash scripts/ci-local.sh tests     (pytest + ruff + bash syntax)
#         bash scripts/ci-local.sh build     (docker image builds)
#         bash scripts/ci-local.sh e2e       (healthcheck gate + integration E2E)
#         bash scripts/ci-local.sh nightly   (full CI + E2E — mirrors nightly deploy)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PASS=0; FAIL=0; SKIP=0
run_step() {
  local name="$1"; shift
  printf "\n━━ %s\n" "$name"
  if "$@"; then
    printf "  ✓ %s\n" "$name"; PASS=$((PASS + 1))
  else
    printf "  ✗ %s\n" "$name"; FAIL=$((FAIL + 1))
  fi
}

skip_step() { printf "\n━━ %s (skipped)\n" "$1"; SKIP=$((SKIP + 1)); }

SCOPE="${1:-all}"

# ── 1. Compose config validation ───────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "compose" ]]; then
  run_step "Compose config (root)" \
    docker compose -f docker-compose.yml config --quiet

  run_step "Compose config (network)" \
    docker compose -f docker-compose.homelab-net.yml -f compose/docker-compose.network.yml config --quiet

  run_step "Compose config (media)" \
    docker compose -f docker-compose.homelab-net.yml -f compose/docker-compose.media.yml config --quiet

  run_step "Compose config (llm)" \
    docker compose -f docker-compose.homelab-net.yml -f compose/docker-compose.llm.yml config --quiet
fi

# ── 2. Compose shell tests ─────────────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "compose" || "$SCOPE" == "tests" ]]; then
  if [[ -f tests/compose/run_tests.sh ]]; then
    run_step "Compose shell tests" bash tests/compose/run_tests.sh
  else
    skip_step "Compose shell tests (not found)"
  fi
fi

# ── 3. Python venv + deps ──────────────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "tests" ]]; then
  PKG_DIR="${ROOT}/src/homelab_workers"
  VENV="${PKG_DIR}/.venv"

  if [[ ! -f "${VENV}/bin/python" ]]; then
    run_step "Create venv + install deps" bash -c "
      python3 -m venv '${VENV}' && \
      '${VENV}/bin/pip' install -q -e '${PKG_DIR}[dev]' && \
      '${VENV}/bin/pip' install -q pydantic && \
      if [[ -d '${ROOT}/media-agent' ]]; then
        '${VENV}/bin/pip' install -q -r '${ROOT}/media-agent/requirements-dev.txt'
      fi
    "
  else
    printf "\n━━ Venv exists, skipping install\n"
  fi

  PY="${VENV}/bin/python"

  # pytest
  run_step "Workers pytest" "$PY" -m pytest -q "${ROOT}/tests/workers"

  if [[ -d "${ROOT}/media-agent/tests" ]]; then
    run_step "Media agent pytest" "$PY" -m pytest -q "${ROOT}/media-agent/tests"
  else
    skip_step "Media agent pytest (no tests dir)"
  fi

  run_step "Package pytest" "$PY" -m pytest -q "${PKG_DIR}/src/homelab_workers/tests"

  # ruff
  run_step "Package ruff" "${VENV}/bin/ruff" check "${PKG_DIR}/src"

  if [[ -d "${ROOT}/media-agent/app" ]]; then
    run_step "Media agent ruff" "${VENV}/bin/ruff" check "${ROOT}/media-agent/app"
  else
    skip_step "Media agent ruff (no app dir)"
  fi
fi

# ── 4. Docker build ────────────────────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "build" || "$SCOPE" == "nightly" ]]; then
  run_step "Build local images" docker compose -f docker-compose.yml build
fi

# ── 5. Bash syntax ─────────────────────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "tests" ]]; then
  check_bash_syntax() {
    local scripts=(
      scripts/setup.sh
      scripts/gh-actions-local.sh
      scripts/fix-media-permissions.sh
      scripts/backup-data.sh
      scripts/render-readme-mermaid.sh
      tests/compose/run_tests.sh
      tests/runtime/run_tests.sh
    )
    shopt -s nullglob
    scripts+=(tests/integration/*.sh)
    local ok=true
    for s in "${scripts[@]}"; do
      if [[ -f "$s" ]]; then
        bash -n "$s" && echo "  OK  ${s##*/}" || { echo "  ERR ${s##*/}"; ok=false; }
      fi
    done
    $ok
  }
  run_step "Bash syntax" check_bash_syntax
fi

# ── 6. README mermaid ──────────────────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "tests" ]]; then
  run_step "README mermaid blocks" python3 -c "
import re, sys, pathlib
c = pathlib.Path('README.md').read_text()
n = len(re.findall(r'\`\`\`mermaid', c))
sys.exit(0 if n >= 1 else 1)
"
fi

# ── 7. Healthcheck gate (e2e / nightly only) ──────────────────────────────
if [[ "$SCOPE" == "e2e" || "$SCOPE" == "nightly" ]]; then
  # Apply any compose config changes and recreate only affected containers.
  run_step "docker compose up (apply config)" docker compose up -d --remove-orphans

  check_healthchecks() {
    local REQUIRED_SERVICES=(caddy media-agent flaresolverr jellyfin plex overseerr internal-dashboard cloudflared dashy openclaw-gateway qbittorrent prowlarr sonarr radarr readarr)

    is_required() {
      local name="$1"
      for svc in "${REQUIRED_SERVICES[@]}"; do
        [[ "$name" == *"$svc"* ]] && return 0
      done
      return 1
    }

    # Poll every 10s until all required services settle (healthy/unhealthy/none), up to 300s.
    echo "  Waiting up to 300s for required services to settle..."
    local deadline=$(( SECONDS + 300 ))
    while (( SECONDS < deadline )); do
      local any_starting=false
      for c in $(docker compose ps --format '{{.Name}}'); do
        local status
        status=$(docker inspect --format='{{.State.Health.Status}}' "$c" 2>/dev/null || echo "none")
        if [[ "$status" == "starting" ]] && is_required "$c"; then
          any_starting=true
          break
        fi
      done
      $any_starting || break
      echo "  waiting... ($((deadline - SECONDS))s left)"
      sleep 10
    done

    # Final status report.
    echo "  Checking container health status..."
    local failed=0
    for c in $(docker compose ps --format '{{.Name}}'); do
      local status
      status=$(docker inspect --format='{{.State.Health.Status}}' "$c" 2>/dev/null || echo "none")
      if [[ "$status" == "unhealthy" || "$status" == "starting" ]]; then
        if is_required "$c"; then
          echo "  FAIL: $c ($status) — required service"
          failed=$((failed + 1))
        else
          echo "  WARN: $c ($status) — non-required"
        fi
      else
        echo "  OK:   $c ($status)"
      fi
    done
    [[ "$failed" -eq 0 ]]
  }
  run_step "Healthcheck gate" check_healthchecks
fi

# ── 8. E2E integration tests (e2e / nightly only) ─────────────────────────
if [[ "$SCOPE" == "e2e" || "$SCOPE" == "nightly" ]]; then
  if [[ -f tests/integration/router-smoke-p0.sh ]]; then
    run_step "E2E: router smoke" bash tests/integration/router-smoke-p0.sh
  else
    skip_step "E2E: router smoke (not found)"
  fi

  if [[ -f tests/integration/media-pipeline-e2e.sh ]]; then
    run_step "E2E: media pipeline" bash tests/integration/media-pipeline-e2e.sh
  else
    skip_step "E2E: media pipeline (not found)"
  fi

  if [[ -f tests/integration/llm-pipeline-e2e.sh ]]; then
    run_step "E2E: LLM pipeline" bash tests/integration/llm-pipeline-e2e.sh
  else
    skip_step "E2E: LLM pipeline (not found)"
  fi

  if [[ -f tests/integration/llm_behavior_check.py ]]; then
    run_step "E2E: LLM behavior check" python3 tests/integration/llm_behavior_check.py
  else
    skip_step "E2E: LLM behavior check (not found)"
  fi
fi

# ── Summary ────────────────────────────────────────────────────────────────
printf "\n══════════════════════════════════════════\n"
printf "  PASS: %d   FAIL: %d   SKIP: %d\n" "$PASS" "$FAIL" "$SKIP"
printf "══════════════════════════════════════════\n"
[[ "$FAIL" -eq 0 ]]
