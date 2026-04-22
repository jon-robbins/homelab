#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

RUNTIME_PROJECT="homelab_runtime_smoke"
RUNTIME_NETWORK="${RUNTIME_PROJECT}_net"
TEST_COMPOSE_FILE="$(mktemp "${TMPDIR:-/tmp}/runtime-compose.XXXXXX.yml")"
SERVICES=("flaresolverr" "internal-dashboard")

declare -i total=0
declare -i passed=0
declare -i failed=0
declare -i skipped=0

log() {
  printf '[runtime-tests] %s\n' "$*"
}

record_pass() {
  local name="$1"
  ((total += 1))
  ((passed += 1))
  log "PASS  ${name}"
}

record_fail() {
  local name="$1"
  local reason="$2"
  ((total += 1))
  ((failed += 1))
  log "FAIL  ${name} (${reason})"
}

record_skip() {
  local name="$1"
  local reason="$2"
  ((total += 1))
  ((skipped += 1))
  log "SKIP  ${name} (${reason})"
}

cleanup() {
  if [[ -f "${TEST_COMPOSE_FILE}" ]]; then
    docker compose -p "${RUNTIME_PROJECT}" -f "${TEST_COMPOSE_FILE}" down --remove-orphans --volumes >/dev/null 2>&1 || true
    rm -f "${TEST_COMPOSE_FILE}"
  fi
}
trap cleanup EXIT

compose() {
  docker compose -p "${RUNTIME_PROJECT}" -f "${TEST_COMPOSE_FILE}" "$@"
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    record_skip "docker-available" "docker CLI not installed"
    return 1
  fi

  if ! docker info >/dev/null 2>&1; then
    record_skip "docker-daemon" "docker daemon not reachable"
    return 1
  fi

  record_pass "docker-ready"
  return 0
}

build_runtime_compose_file() {
  local fixture_path="${REPO_ROOT}/tests/runtime/fixtures/internal-dashboard"
  cat >"${TEST_COMPOSE_FILE}" <<EOF
services:
  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    security_opt:
      - no-new-privileges:true
    environment:
      - LOG_LEVEL=info
      - TZ=\${TZ:-UTC}
    networks:
      - runtime_net

  internal-dashboard:
    image: nginx:alpine
    security_opt:
      - no-new-privileges:true
    volumes:
      - ${fixture_path}:/usr/share/nginx/html:ro
    networks:
      - runtime_net

networks:
  runtime_net:
    name: ${RUNTIME_NETWORK}
    driver: bridge
EOF
}

start_subset() {
  if compose up -d "${SERVICES[@]}"; then
    record_pass "compose-up"
  else
    record_fail "compose-up" "failed to start selected services"
    return 1
  fi
}

wait_for_running() {
  local service
  local id
  local state
  local attempt
  local max_attempts=30

  for service in "${SERVICES[@]}"; do
    id="$(compose ps -q "${service}")"
    if [[ -z "${id}" ]]; then
      record_fail "container-${service}-exists" "container was not created"
      continue
    fi

    attempt=0
    while ((attempt < max_attempts)); do
      state="$(docker inspect -f '{{.State.Status}}' "${id}" 2>/dev/null || true)"
      if [[ "${state}" == "running" ]]; then
        record_pass "container-${service}-running"
        break
      fi
      sleep 1
      ((attempt += 1))
    done

    if [[ "${state}" != "running" ]]; then
      record_fail "container-${service}-running" "state=${state:-unknown}"
    fi
  done
}

http_check() {
  local name="$1"
  local url="$2"
  local expected_regex="$3"
  local max_attempts="${4:-20}"
  local sleep_seconds="${5:-2}"
  local attempt=0
  local output
  local status

  while ((attempt < max_attempts)); do
    if docker run --rm --network "${RUNTIME_NETWORK}" curlimages/curl:8.7.1 \
      -sS -m 10 -w ' HTTP_STATUS:%{http_code}' "${url}" >"/tmp/${RUNTIME_PROJECT}-${name}.out" 2>&1; then
      output="$(<"/tmp/${RUNTIME_PROJECT}-${name}.out")"
      status="${output##*HTTP_STATUS:}"
      if [[ -n "${expected_regex}" ]] && [[ ! "${status}" =~ ${expected_regex} ]]; then
        sleep "${sleep_seconds}"
        ((attempt += 1))
        continue
      fi
      record_pass "${name}"
      return 0
    fi
    sleep "${sleep_seconds}"
    ((attempt += 1))
  done

  if [[ -f "/tmp/${RUNTIME_PROJECT}-${name}.out" ]]; then
    output="$(<"/tmp/${RUNTIME_PROJECT}-${name}.out")"
    status="${output##*HTTP_STATUS:}"
  fi
  record_fail "${name}" "endpoint did not become healthy (status=${status:-unknown})"
  return 1
}

run_endpoint_checks() {
  local service
  for service in "${SERVICES[@]}"; do
    case "${service}" in
      flaresolverr)
        # / responds 200 on healthy startup and confirms in-network HTTP reachability.
        http_check "endpoint-flaresolverr-root" "http://flaresolverr:8191/" '^(200)$'
        ;;
      internal-dashboard)
        http_check "endpoint-internal-dashboard-index" "http://internal-dashboard/index.html" '^(200)$'
        ;;
    esac
  done
}

main() {
  log "Runtime smoke tests start"

  if ! require_docker; then
    log "Runtime smoke tests complete: total=${total}, passed=${passed}, failed=${failed}, skipped=${skipped}"
    exit 0
  fi

  build_runtime_compose_file
  record_pass "runtime-subset-selection"

  if ! start_subset; then
    log "Runtime smoke tests complete: total=${total}, passed=${passed}, failed=${failed}, skipped=${skipped}"
    exit 1
  fi

  wait_for_running
  run_endpoint_checks

  log "Runtime smoke tests complete: total=${total}, passed=${passed}, failed=${failed}, skipped=${skipped}"
  if ((failed > 0)); then
    exit 1
  fi
}

main "$@"
