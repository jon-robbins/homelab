#!/usr/bin/env bash
# Phased post-Docker boot orchestrator for the homelab stack.
# Phases: P0 mounts → P1 docker → P2 mount-bind recreate → P3 Tailscale IP →
#         P4 TS-bound recreate → P5 settle + status.
#
# Usage:
#   homelab-boot-orchestrator.sh              # run all phases
#   homelab-boot-orchestrator.sh --dry-run    # skip recreates; waits still apply
#   homelab-boot-orchestrator.sh --phases P3,P4
#   homelab-boot-orchestrator.sh --status     # print status.json if present
#
# Status: ${ROOT}/data/boot-orchestrator/status.json
# Docs: docs/boot-orchestrator.md
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/ops/verify-media-mounts.sh disable=SC1091
source "${ROOT}/scripts/ops/verify-media-mounts.sh"
# shellcheck source=scripts/lib/managed-secrets.sh disable=SC1091
source "${ROOT}/scripts/lib/managed-secrets.sh"

STATUS_DIR="${ROOT}/data/boot-orchestrator"
STATUS_PATH="${STATUS_DIR}/status.json"
PROGRESS_PATH="${STATUS_DIR}/progress.json"

MOUNT_WAIT_SEC="${HOMELAB_BOOT_MOUNT_WAIT_SEC:-120}"
DOCKER_WAIT_SEC="${HOMELAB_BOOT_DOCKER_WAIT_SEC:-60}"
TS_WAIT_SEC="${TAILSCALE_BIND_WAIT_SEC:-180}"
SETTLE_SEC="${HOMELAB_BOOT_SETTLE_SEC:-120}"

# Compose service names (root docker-compose.yml) that bind media paths.
DEFAULT_MOUNT_BIND_SERVICES=(
  qbittorrent
  jellyfin
  plex
  radarr
  sonarr
  bazarr
  readarr
  filebrowser
  homelab-qbt-offload
)
DEFAULT_TS_BIND_SERVICES=(caddy homelab-mcp)
# P6 hard-fail only these if Docker reports unhealthy after settle.
DEFAULT_CRITICAL_HEALTH_SERVICES=(caddy qbittorrent cloudflared homelab-qbt-offload)

DRY_RUN=0
PHASES_FILTER=""
STATUS_ONLY=0
# Soft warnings carried into final status (pipe-separated reason codes).
SOFT_WARNINGS=""

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

env_get() {
  local key="$1"
  local val="${!key:-}"
  if [[ -n "${val}" ]]; then
    printf '%s' "${val}"
    return 0
  fi
  if [[ -f "${ROOT}/.env" ]]; then
    awk -F= -v k="${key}" '
      $0 ~ "^" k "=" {
        sub(/\r$/, "")
        print substr($0, index($0, "=") + 1)
        exit
      }
    ' "${ROOT}/.env"
  fi
}

boot_id() {
  if [[ -r /proc/sys/kernel/random/boot_id ]]; then
    tr -d '[:space:]' </proc/sys/kernel/random/boot_id
  else
    printf 'unknown'
  fi
}

add_warning() {
  local code="$1"
  if [[ -z "${SOFT_WARNINGS}" ]]; then
    SOFT_WARNINGS="${code}"
  elif [[ "|${SOFT_WARNINGS}|" != *"|${code}|"* ]]; then
    SOFT_WARNINGS="${SOFT_WARNINGS}|${code}"
  fi
}

write_status() {
  local ok="$1" phase="$2" reason="$3" remediation="$4"
  local detail="${5:-}"
  local now dry_json
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [[ "${DRY_RUN}" -eq 1 ]]; then dry_json=true; else dry_json=false; fi
  mkdir -p "${STATUS_DIR}"
  OK_JSON="${ok}" PHASE="${phase}" REASON="${reason}" REMEDIATION="${remediation}" \
    DETAIL="${detail}" UPDATED_AT="${now}" DRY_JSON="${dry_json}" \
    WARNINGS="${SOFT_WARNINGS}" STATUS_PATH="${STATUS_PATH}" \
    python3 - <<'PY'
import json, os
from pathlib import Path
warnings = [w for w in os.environ.get("WARNINGS", "").split("|") if w]
payload = {
    "ok": os.environ["OK_JSON"] == "true",
    "phase": os.environ["PHASE"],
    "reason": os.environ["REASON"],
    "remediation": os.environ["REMEDIATION"],
    "detail": os.environ.get("DETAIL", ""),
    "warnings": warnings,
    "updated_at": os.environ["UPDATED_AT"],
    "dry_run": os.environ["DRY_JSON"] == "true",
}
Path(os.environ["STATUS_PATH"]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
}

fail() {
  local phase="$1" reason="$2" remediation="$3" detail="${4:-}"
  log "ERROR phase=${phase} reason=${reason}: ${detail:-}"
  write_status false "${phase}" "${reason}" "${remediation}" "${detail}"
  exit 1
}

# shellcheck disable=SC2317
docker_ready() { docker info >/dev/null 2>&1; }

want_phase() {
  local p="$1"
  [[ -z "${PHASES_FILTER}" ]] && return 0
  [[ ",${PHASES_FILTER}," == *",${p},"* ]]
}

wait_until() {
  local label="$1" timeout="$2"
  shift 2
  local deadline=$((SECONDS + timeout))
  until "$@"; do
    if (( SECONDS >= deadline )); then
      return 1
    fi
    sleep 2
  done
  log "${label}: ready"
  return 0
}

load_service_list() {
  local env_name="$1"
  local -n _out="$2"
  shift 2
  local raw
  raw="$(env_get "${env_name}")"
  if [[ -n "${raw}" ]]; then
    # shellcheck disable=SC2206
    _out=(${raw//,/ })
  else
    _out=("$@")
  fi
}

# Progress file scopes recreate skips to the current host boot (systemd retries).
init_progress() {
  mkdir -p "${STATUS_DIR}"
  local current
  current="$(boot_id)"
  BOOT_ID="${current}" PROGRESS_PATH="${PROGRESS_PATH}" python3 - <<'PY'
import json, os
from pathlib import Path
path = Path(os.environ["PROGRESS_PATH"])
current = os.environ["BOOT_ID"]
data = {"boot_id": current, "completed": []}
if path.exists():
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
        if prev.get("boot_id") == current and isinstance(prev.get("completed"), list):
            data = prev
    except Exception:
        pass
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

phase_done() {
  local phase="$1"
  [[ -f "${PROGRESS_PATH}" ]] || return 1
  PHASE="${phase}" PROGRESS_PATH="${PROGRESS_PATH}" python3 - <<'PY'
import json, os, sys
from pathlib import Path
data = json.loads(Path(os.environ["PROGRESS_PATH"]).read_text(encoding="utf-8"))
sys.exit(0 if os.environ["PHASE"] in data.get("completed", []) else 1)
PY
}

mark_phase_done() {
  local phase="$1"
  local current
  [[ "${DRY_RUN}" -eq 1 ]] && return 0
  current="$(boot_id)"
  mkdir -p "${STATUS_DIR}"
  PHASE="${phase}" BOOT_ID="${current}" PROGRESS_PATH="${PROGRESS_PATH}" python3 - <<'PY'
import json, os
from pathlib import Path
path = Path(os.environ["PROGRESS_PATH"])
boot = os.environ["BOOT_ID"]
phase = os.environ["PHASE"]
data = {"boot_id": boot, "completed": []}
if path.exists():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
if data.get("boot_id") != boot:
    data = {"boot_id": boot, "completed": []}
completed = list(data.get("completed") or [])
if phase not in completed:
    completed.append(phase)
data["completed"] = completed
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

# Verify persistent Agent secret files exist, are readable, and are non-empty.
# Returns 0 if all keys are ready, nonzero on failure. Only prints key names and reason codes.
check_rendered_secret_files() {
  local secret_dir="${1:-${HOMELAB_SECRET_DIR:-${HOME}/.config/homelab/secrets}}"
  shift || true
  local keys=("$@")
  if [[ ${#keys[@]} -eq 0 ]]; then
    local key
    while IFS= read -r key; do
      [[ -n "${key}" ]] && keys+=("${key}")
    done < <(managed_secret_keys)
  fi

  local failed=0
  if [[ ! -d "${secret_dir}" ]]; then
    log "ERROR: secret directory missing: ${secret_dir}"
    for key in "${keys[@]}"; do
      printf 'missing: %s\n' "${key}" >&2
    done
    return 1
  fi

  for key in "${keys[@]}"; do
    local f="${secret_dir}/${key}"
    if [[ ! -e "${f}" ]]; then
      log "ERROR: secret file missing: ${key}"
      printf 'missing: %s\n' "${key}" >&2
      failed=1
    elif [[ ! -r "${f}" ]]; then
      log "ERROR: secret file unreadable: ${key}"
      printf 'unreadable: %s\n' "${key}" >&2
      failed=1
    elif [[ ! -s "${f}" ]]; then
      log "ERROR: secret file empty: ${key}"
      printf 'empty: %s\n' "${key}" >&2
      failed=1
    fi
  done

  if (( failed != 0 )); then
    return 1
  fi
  return 0
}

compose_container_id() {
  local svc="$1"
  docker compose -f "${ROOT}/docker-compose.yml" --project-directory "${ROOT}" ps -q "${svc}" 2>/dev/null | head -n1
}

compose_recreate() {
  local svc
  local wrapper="${ROOT}/scripts/ops/homelab-compose.py"
  local compose_services=""
  cd "${ROOT}"

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    for svc in "$@"; do
      log "dry-run: would force-recreate ${svc}"
    done
    return 0
  fi

  if ! check_rendered_secret_files "${HOMELAB_SECRET_DIR:-${HOME}/.config/homelab/secrets}"; then
    log "ERROR: rendered secret files check failed before Compose mutation"
    return 1
  fi

  if [[ ! -x "${wrapper}" ]]; then
    log "ERROR: missing ${wrapper}"
    return 1
  fi

  # Resolve membership once. Distinguishes Infisical/Compose failure from a
  # genuinely absent service name (do not mask auth failures as "missing").
  if ! compose_services="$("${wrapper}" config --services)"; then
    log "ERROR: secret-aware Compose config failed during membership check"
    return 1
  fi

  for svc in "$@"; do
    if ! grep -qx "${svc}" <<<"${compose_services}"; then
      log "skip missing compose service: ${svc}"
      continue
    fi
    log "force-recreate ${svc}"
    if ! "${wrapper}" up -d --no-deps --force-recreate "${svc}"; then
      return 1
    fi
  done
  return 0
}

# --- Phase implementations ---

phase_p0_mounts() {
  log "P0: waiting up to ${MOUNT_WAIT_SEC}s for media mounts"
  MEDIA_HDD_PATH="$(env_get MEDIA_HDD_PATH)"
  MEDIA_NVME_PATH="$(env_get MEDIA_NVME_PATH)"
  export MEDIA_HDD_PATH="${MEDIA_HDD_PATH:-/mnt/media-hdd}"
  export MEDIA_NVME_PATH="${MEDIA_NVME_PATH:-/mnt/media-nvme}"

  if ! wait_until "P0 mounts" "${MOUNT_WAIT_SEC}" verify_host_media_mounts "${ROOT}"; then
    fail P0 mounts_not_ready \
      "Check findmnt/disk power/fstab UUID; remount; re-run orchestrator" \
      "HDD=${MEDIA_HDD_PATH} NVMe=${MEDIA_NVME_PATH} not ready within ${MOUNT_WAIT_SEC}s"
  fi
  mark_phase_done P0
  write_status true P0 ok "" "Media mounts verified"
}

phase_p1_docker() {
  log "P1: waiting up to ${DOCKER_WAIT_SEC}s for Docker"
  if ! wait_until "P1 docker" "${DOCKER_WAIT_SEC}" docker_ready; then
    fail P1 docker_not_ready \
      "systemctl status docker; journalctl -u docker" \
      "docker info failed within ${DOCKER_WAIT_SEC}s"
  fi
  mark_phase_done P1
  write_status true P1 ok "" "Docker ready"
}

phase_p2_mount_binds() {
  local services=()
  # shellcheck disable=SC2034
  load_service_list HOMELAB_BOOT_MOUNT_BIND_SERVICES services "${DEFAULT_MOUNT_BIND_SERVICES[@]}"

  if phase_done P2; then
    log "P2: already completed this boot — skip force-recreate (verify only)"
  else
    if ! check_rendered_secret_files "${HOMELAB_SECRET_DIR:-${HOME}/.config/homelab/secrets}"; then
      fail P2 rendered_secrets_not_ready \
        "Check systemctl status homelab-infisical-agent.service and rendered files in ${HOMELAB_SECRET_DIR:-${HOME}/.config/homelab/secrets}" \
        "rendered secret files missing, unreadable, or empty"
    fi
    log "P2: recreate mount-bind services (${#services[@]}): ${services[*]}"
    if ! compose_recreate "${services[@]}"; then
      fail P2 mount_bind_recreate_failed \
        "Inspect docker compose logs for failed service; fix mount/permissions; re-run" \
        "one or more mount-bind recreates failed"
    fi
  fi

  # Spot-check high-risk services via compose IDs (not hardcoded container names).
  # Mark P2 done only after verifies pass so systemd retries can still recreate.
  local svc cid path
  for pair in \
    "homelab-qbt-offload:${MEDIA_HDD_PATH:-/mnt/media-hdd}" \
    "jellyfin:/movies" \
    "qbittorrent:/seedstore" \
    "filebrowser:/srv/hdd"; do
    svc="${pair%%:*}"
    path="${pair#*:}"
    cid="$(compose_container_id "${svc}")"
    [[ -n "${cid}" ]] || continue
    if docker inspect -f '{{.State.Running}}' "${cid}" 2>/dev/null | grep -qx true; then
      if ! verify_container_media_path "${cid}" "${path}"; then
        fail P2 stale_media_bind \
          "docker compose up -d --no-deps --force-recreate ${svc} after mounts are real" \
          "${svc} ${path}"
      fi
    fi
  done
  mark_phase_done P2
  write_status true P2 ok "" "Mount-bind services ready"
}

phase_p3_tailscale() {
  local ts_ip
  ts_ip="$(env_get TAILSCALE_IP)"
  if [[ -z "${ts_ip}" ]]; then
    fail P3 tailscale_ip_missing \
      "Set TAILSCALE_IP in .env; ensure compose service tailscale is up" \
      "TAILSCALE_IP unset"
  fi
  log "P3: waiting up to ${TS_WAIT_SEC}s for Tailscale IP ${ts_ip} on tailscale0"
  # shellcheck disable=SC2317
  ip_ready() { ip -4 -o addr show dev tailscale0 2>/dev/null | grep -Fq " ${ts_ip}/"; }
  if ! wait_until "P3 tailscale" "${TS_WAIT_SEC}" ip_ready; then
    fail P3 tailscale_ip_missing \
      "Wait/fix Tailscale container; check .env TAILSCALE_IP matches interface" \
      "${ts_ip} not on tailscale0 within ${TS_WAIT_SEC}s"
  fi
  mark_phase_done P3
  write_status true P3 ok "" "Tailscale IP ready: ${ts_ip}"
}

phase_p4_ts_binds() {
  local services=()
  # shellcheck disable=SC2034
  load_service_list HOMELAB_BOOT_TS_BIND_SERVICES services "${DEFAULT_TS_BIND_SERVICES[@]}"

  if phase_done P4; then
    log "P4: already completed this boot — skip force-recreate"
  else
    log "P4: recreate Tailscale-bound services: ${services[*]}"
    if ! compose_recreate "${services[@]}"; then
      fail P4 tailscale_bind_recreate_failed \
        "Recreate caddy/homelab-mcp after Tailscale IP is present" \
        "TS-bound recreate failed"
    fi
    mark_phase_done P4
  fi
  write_status true P4 ok "" "Tailscale-bound services ready"
}

workers_config_path() {
  local configured
  configured="$(env_get WORKERS_CONFIG_PATH)"
  if [[ -n "${configured}" && -f "${configured}" ]]; then
    printf '%s' "${configured}"
    return 0
  fi
  if [[ -f "${ROOT}/config/workers.yaml.example" ]]; then
    printf '%s' "${ROOT}/config/workers.yaml.example"
  fi
}

run_ingress_smoke() {
  local config_path
  config_path="$(workers_config_path)"
  [[ -n "${config_path}" && -f "${config_path}" ]] || return 0
  INGRESS_SMOKE_CONFIG="${config_path}" ROOT="${ROOT}" python3 - <<'PY'
from __future__ import annotations

import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml


def bool_value(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def env_map(root: Path) -> dict[str, str]:
    data = dict(os.environ)
    env_path = root / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            data.setdefault(key.strip(), value.strip().strip("'\""))
    return data


def expand_vars(value: str, values: dict[str, str]) -> str:
    return re.sub(r"\$\{([^}]+)\}", lambda match: values.get(match.group(1), ""), value)


def probe(url: str, host_header: str, timeout_seconds: float) -> int | None:
    headers = {"User-Agent": "homelab-boot-orchestrator/ingress-smoke"}
    if host_header:
        headers["Host"] = host_header
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return None


root = Path(os.environ["ROOT"])
config_path = Path(os.environ["INGRESS_SMOKE_CONFIG"])
try:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
except Exception as exc:
    print(f"ingress_smoke: config_error {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(1)

smoke = ((config.get("boot") or {}).get("ingress_smoke") or {})
if not bool_value(smoke.get("enabled"), False):
    sys.exit(0)

required = bool_value(smoke.get("required"), True)
wait_seconds = int(smoke.get("wait_seconds") or 0)
timeout_seconds = float(smoke.get("timeout_seconds") or 5)
routes = smoke.get("routes") or []
if not isinstance(routes, list) or not routes:
    sys.exit(0)

values = env_map(root)
deadline = time.monotonic() + max(0, wait_seconds)
last_error = "not_run"
while True:
    failures: list[str] = []
    for route in routes:
        if not isinstance(route, dict):
            failures.append("invalid_route")
            continue
        name = str(route.get("name") or "unnamed")
        url = expand_vars(str(route.get("url") or ""), values)
        host_header = expand_vars(str(route.get("host_header") or ""), values)
        accepted = {int(code) for code in (route.get("accepted_statuses") or [200])}
        status = probe(url, host_header, timeout_seconds) if url else None
        if status not in accepted:
            failures.append(f"{name} status={status or 'connect_error'} url={url}")
    if not failures:
        print("ingress_smoke: ok")
        sys.exit(0)
    last_error = "; ".join(failures)
    if time.monotonic() >= deadline:
        print(f"ingress_smoke: failed {last_error}", file=sys.stderr)
        sys.exit(1 if required else 2)
    time.sleep(2)
PY
}

phase_p6_settle() {
  log "P6: settle ${SETTLE_SEC}s then health snapshot"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "dry-run: skip settle sleep"
  else
    sleep "${SETTLE_SEC}"
  fi

  local critical=()
  # shellcheck disable=SC2034
  load_service_list HOMELAB_BOOT_CRITICAL_HEALTH_SERVICES critical "${DEFAULT_CRITICAL_HEALTH_SERVICES[@]}"

  local unhealthy_all=() critical_unhealthy=()
  local svc cid health
  for svc in "${critical[@]}"; do
    cid="$(compose_container_id "${svc}")"
    [[ -n "${cid}" ]] || continue
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${cid}" 2>/dev/null || printf 'unknown')"
    if [[ "${health}" == "unhealthy" ]]; then
      critical_unhealthy+=("${svc}")
      unhealthy_all+=("${svc}")
    fi
  done

  # Informational: any other unhealthy containers (not hard-fail).
  local line name
  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    name="${line%%|*}"
    unhealthy_all+=("${name}")
  done < <(docker ps --format '{{.Names}}|{{.Status}}' 2>/dev/null | awk -F'|' '
    $2 ~ /unhealthy/ { print $1 "|unhealthy" }')

  # Soft: offload last_run_failed (status file) after settle.
  local offload_status="${ROOT}/data/qbt-offload/status.json"
  if [[ -f "${offload_status}" ]]; then
    if OFFLOAD_STATUS="${offload_status}" python3 - <<'PY'
import json, os, sys
from pathlib import Path
data = json.loads(Path(os.environ["OFFLOAD_STATUS"]).read_text(encoding="utf-8"))
sys.exit(0 if int(data.get("failed") or 0) > 0 else 1)
PY
    then
      log "P6: WARN last_run_failed — qbt-offload status failed>0"
      add_warning last_run_failed
    fi
  fi

  if ((${#critical_unhealthy[@]} > 0)); then
    write_status false P6 dependency_not_ready \
      "After settle, inspect logs/status.json for each unhealthy critical service; do not blind-restart" \
      "critical unhealthy: ${critical_unhealthy[*]}"
    log "ERROR P6 critical unhealthy after settle: ${critical_unhealthy[*]}"
    exit 1
  fi

  local smoke_rc=0
  run_ingress_smoke || smoke_rc=$?
  if [[ "${smoke_rc}" -eq 1 ]]; then
    fail P6 ingress_route_not_ready \
      "Check Tailscale IP, Caddy route/bind, qBittorrent WebUI, and the boot.ingress_smoke YAML route" \
      "one or more configured ingress smoke routes failed"
  elif [[ "${smoke_rc}" -eq 2 ]]; then
    log "P6: WARN ingress_route_not_ready"
    add_warning ingress_route_not_ready
  fi

  mark_phase_done P6
  local final_reason=ok
  local final_remediation=""
  if [[ -n "${SOFT_WARNINGS}" ]]; then
    final_reason="${SOFT_WARNINGS%%|*}"
    final_remediation="See warnings[]; boot completed with soft issues"
  fi
  write_status true P6 "${final_reason}" "${final_remediation}" \
    "Boot orchestrator complete; unhealthy_seen: ${unhealthy_all[*]:-none}"
  log "P6: critical services healthy (or no healthcheck); warnings=${SOFT_WARNINGS:-none}"
}

run_phases() {
  if want_phase P0; then phase_p0_mounts; fi
  if want_phase P1; then phase_p1_docker; fi
  if want_phase P2; then phase_p2_mount_binds; fi
  if want_phase P3; then phase_p3_tailscale; fi
  if want_phase P4; then phase_p4_ts_binds; fi
  if want_phase P6; then phase_p6_settle; fi
}

usage() {
  cat <<'EOF'
Usage: homelab-boot-orchestrator.sh [--dry-run] [--phases P0,P1,...] [--status]

  --dry-run   Skip force-recreate; wait/probe gates still run
              and may fail. Rewrites status.json.
  --phases    Comma-separated subset (e.g. P3,P4)
  --status    Print data/boot-orchestrator/status.json
EOF
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY_RUN=1; shift ;;
      --phases) PHASES_FILTER="${2// /}"; shift 2 ;;
      --status) STATUS_ONLY=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) usage >&2; exit 2 ;;
    esac
  done

  if [[ "${STATUS_ONLY}" -eq 1 ]]; then
    if [[ -f "${STATUS_PATH}" ]]; then
      cat "${STATUS_PATH}"
      exit 0
    fi
    printf '{"ok":false,"reason":"missing_status"}\n'
    exit 1
  fi

  log "homelab-boot-orchestrator start root=${ROOT} dry_run=${DRY_RUN} phases=${PHASES_FILTER:-all}"
  init_progress
  run_phases
  log "homelab-boot-orchestrator done"
  exit 0
fi
