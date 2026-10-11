#!/usr/bin/env bash
# ── Local CI: mirrors .github/workflows/ci.yml and deploy preflight ────────
# Usage:  bash scripts/ci/ci-local.sh            (run all CI checks — no E2E)
#         bash scripts/ci/ci-local.sh validate   (mirror ci.yml validate job — pre-nightly)
#         bash scripts/ci/ci-local.sh compose    (compose config + shell tests)
#         bash scripts/ci/ci-local.sh tests      (pytest + ruff + bash syntax)
#         bash scripts/ci/ci-local.sh build        (docker image builds)
#         bash scripts/ci/ci-local.sh e2e          (explicit live integration E2E)
#         bash scripts/ci/ci-local.sh nightly      (validate + healthcheck gate; no media E2E)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
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

# Placeholders only for non-mutating Compose parse/build. Never export into the
# parent shell for live e2e/nightly mutations (would inject CI_PLACEHOLDER).
with_ci_secret_placeholders() {
  # shellcheck source=/dev/null
  (
    # shellcheck source=/dev/null
    source "${ROOT}/scripts/ci/export-ci-secret-placeholders.sh"
    "$@"
  )
}

SCOPE="${1:-all}"

case "$SCOPE" in
  all|compose|tests|validate|build) ;;
  *) echo "FATAL: Scope '$SCOPE' is quarantined." >&2; exit 2 ;;
esac

export SKIP_HOST_PORT_PREFLIGHT=1
export COMPOSE_ENV_FILES=/dev/null

# Live mutation modes must not inherit CI placeholders.
if [[ "$SCOPE" == "e2e" || "$SCOPE" == "nightly" ]]; then
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    if [[ "${!key:-}" == "CI_PLACEHOLDER" ]]; then
      unset "$key"
    fi
  done < <(
    # shellcheck source=/dev/null
    source "${ROOT}/scripts/lib/managed-secrets.sh"
    managed_secret_keys
  )
fi

# ── 1. Compose config validation ───────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "compose" || "$SCOPE" == "validate" || "$SCOPE" == "nightly" ]]; then
  if [[ "$SCOPE" == "nightly" ]]; then
    # Validate with real Infisical before any mutation.
    run_step "Compose config (Infisical)" \
      "${ROOT}/scripts/ops/homelab-compose.py" config --quiet
  else
    run_step "Compose config (root bundle)" \
      with_ci_secret_placeholders docker compose -f docker-compose.yml config --quiet
  fi
fi

# ── 2. Compose shell tests ─────────────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "compose" || "$SCOPE" == "tests" || "$SCOPE" == "validate" || "$SCOPE" == "nightly" ]]; then
  if [[ -f tests/compose/run_tests.sh ]]; then
    run_step "Compose shell tests" bash tests/compose/run_tests.sh
  else
    skip_step "Compose shell tests (not found)"
  fi
fi

# ── 2a. Ops shell tests ──────────────────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "tests" || "$SCOPE" == "validate" || "$SCOPE" == "nightly" ]]; then
  if [[ -f tests/ops/run_tests.sh ]]; then
    run_step "Ops shell tests" bash tests/ops/run_tests.sh
  else
    skip_step "Ops shell tests (not found)"
  fi
fi

# ── 2b. Doc validation ───────────────────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "validate" || "$SCOPE" == "nightly" ]]; then
  run_step "Doc lint" bash tests/docs/run_tests.sh
fi

# ── 3. Python venv + deps ──────────────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "tests" || "$SCOPE" == "validate" || "$SCOPE" == "nightly" ]]; then
  PKG_DIR="${ROOT}/apps/homelab-workers"
  VENV="${PKG_DIR}/.venv"

  if [[ ! -f "${VENV}/bin/python" ]]; then
    run_step "Create venv + install deps" bash -c "
      python3 -m venv '${VENV}' && \
      '${VENV}/bin/pip' install -q -e '${PKG_DIR}[dev]' && \
      '${VENV}/bin/pip' install -q pydantic && \
      if [[ -d '${ROOT}/apps/media-agent' ]]; then
        '${VENV}/bin/pip' install -q -r '${ROOT}/apps/media-agent/requirements-dev.txt'
      fi
    "
  else
    printf "\n━━ Venv exists, skipping install\n"
  fi

  PY="${VENV}/bin/python"

  if [[ -d "${ROOT}/apps/media-agent/tests" ]]; then
    run_step "Media agent pytest" "$PY" -m pytest -q \
      -c "${ROOT}/apps/media-agent/pyproject.toml" \
      "${ROOT}/apps/media-agent/tests"
  else
    skip_step "Media agent pytest (no tests dir)"
  fi

  run_step "Package pytest" "$PY" -m pytest -q "${PKG_DIR}/src/homelab_workers/tests"

  if [[ -f "${ROOT}/tests/ci/test_workflow_security.py" ]]; then
    run_step "Workflow security pytest" "$PY" -m pytest -q "${ROOT}/tests/ci/test_workflow_security.py"
  fi

  # ruff
  run_step "Package ruff" "${VENV}/bin/ruff" check \
    "${PKG_DIR}/src" \
    "${PKG_DIR}/src/homelab_workers/tests"

  if [[ -d "${ROOT}/apps/media-agent/app" ]]; then
    run_step "Media agent ruff" "${VENV}/bin/ruff" check \
      "${ROOT}/apps/media-agent/app" \
      "${ROOT}/apps/media-agent/tests"
  else
    skip_step "Media agent ruff (no app dir)"
  fi
fi

# ── 4. Docker build ────────────────────────────────────────────────────────
if [[ "$SCOPE" == "build" ]]; then
  run_step "Build local images" \
    with_ci_secret_placeholders docker compose -f docker-compose.yml build
fi

# ── 5. Bash syntax ─────────────────────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "tests" || "$SCOPE" == "validate" || "$SCOPE" == "nightly" ]]; then
  check_bash_syntax() {
    shopt -s nullglob globstar
    local scripts=(
      scripts/ci/*.sh
      scripts/ops/*.sh
      scripts/hardening/*.sh
      tests/compose/*.sh
      tests/docs/*.sh
      tests/runtime/*.sh
      tests/integration/*.sh
    )
    local ok=true
    for s in "${scripts[@]}"; do
      if [[ -f "$s" ]]; then
        if bash -n "$s"; then
          echo "  OK  ${s}"
        else
          echo "  ERR ${s}"
          ok=false
        fi
      fi
    done
    $ok
  }
  run_step "Bash syntax" check_bash_syntax
fi

# ── 6. README mermaid ──────────────────────────────────────────────────────
if [[ "$SCOPE" == "all" || "$SCOPE" == "tests" || "$SCOPE" == "validate" || "$SCOPE" == "nightly" ]]; then
  run_step "README mermaid blocks" python3 -c "
import re, sys, pathlib
c = pathlib.Path('README.md').read_text()
n = len(re.findall(r'\`\`\`mermaid', c))
sys.exit(0 if n >= 1 else 1)
"
fi

# ── 7. Healthcheck gate (e2e / nightly only) ──────────────────────────────
if [[ "$SCOPE" == "e2e" || "$SCOPE" == "nightly" ]]; then
  if [[ ! -x "${ROOT}/scripts/ops/homelab-compose.py" ]]; then
    echo "FAIL: live ${SCOPE} requires scripts/ops/homelab-compose.py" >&2
    exit 1
  fi
  if [[ ! -f "${HOMELAB_INFISICAL_BOOTSTRAP:-${HOME}/.config/homelab/infisical.env}" ]]; then
    echo "FAIL: live ${SCOPE} requires Infisical bootstrap; refusing to mutate with placeholders" >&2
    exit 1
  fi
  # Apply any compose config changes via secret-aware wrapper (never CI_PLACEHOLDER).
  run_step "homelab-compose up (apply config)" \
    "${ROOT}/scripts/ops/homelab-compose.py" up -d --remove-orphans

  check_healthchecks() {
    local REQUIRED_SERVICES=(caddy media-agent flaresolverr jellyfin plex overseerr internal-dashboard cloudflared qbittorrent prowlarr sonarr radarr readarr)

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

# ── 8. E2E integration tests (explicit e2e only) ──────────────────────────
if [[ "$SCOPE" == "e2e" ]]; then
  if [[ -f tests/integration/router-smoke-p0.sh ]]; then
    run_step "E2E: router smoke" bash tests/integration/router-smoke-p0.sh
  else
    skip_step "E2E: router smoke (not found)"
  fi
fi

# ── Summary ────────────────────────────────────────────────────────────────
printf "\n══════════════════════════════════════════\n"
printf "  PASS: %d   FAIL: %d   SKIP: %d\n" "$PASS" "$FAIL" "$SKIP"
printf "══════════════════════════════════════════\n"
[[ "$FAIL" -eq 0 ]]
