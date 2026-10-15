#!/usr/bin/env bash
# Host media-mount verification helpers.
# Can be sourced or executed standalone.
#
# Standalone usage:
#   verify-media-mounts.sh              # exit 0 if HDD+NVMe look real
#   verify-media-mounts.sh --json       # print JSON summary
#   verify-media-mounts.sh --container NAME PATH
#       check that container PATH is not bound to the OS root LV
set -euo pipefail

# shellcheck disable=SC2034  # exported for callers that source this file
HOMELAB_MOUNT_VERIFY_VERSION=1

# Size floors in KiB (df -Pk). Stub mounts on the OS disk fail these.
MEDIA_HDD_MIN_KIB="${MEDIA_HDD_MIN_KIB:-2147483648}"   # 2 TiB
MEDIA_NVME_MIN_KIB="${MEDIA_NVME_MIN_KIB:-1073741824}" # 1 TiB

_env_from_dotenv() {
  local key="$1" root="${2:-}"
  local env_file="${root}/.env"
  [[ -f "${env_file}" ]] || return 0
  awk -F= -v k="${key}" '
    $0 ~ "^" k "=" {
      sub(/\r$/, "")
      print substr($0, index($0, "=") + 1)
      exit
    }
  ' "${env_file}"
}

mount_source() {
  local path="$1"
  findmnt -n -o SOURCE --target "${path}" 2>/dev/null || true
}

mount_fstype() {
  local path="$1"
  findmnt -n -o FSTYPE --target "${path}" 2>/dev/null || true
}

df_kib_total() {
  local path="$1"
  # df -Pk: POSIX, 1024-byte blocks; field 2 = 1K-blocks
  df -Pk "${path}" 2>/dev/null | awk 'NR==2 {print $2; exit}'
}

df_source() {
  local path="$1"
  df -Pk "${path}" 2>/dev/null | awk 'NR==2 {print $1; exit}'
}

is_os_root_source() {
  local source="$1"
  local root_source
  root_source="$(df_source /)"
  [[ -n "${source}" && -n "${root_source}" && "${source}" == "${root_source}" ]]
}

# Returns 0 if path is a real media mount (not OS root stub).
verify_host_media_path() {
  local path="$1"
  local min_kib="$2"
  local label="$3"
  local src total

  if [[ ! -d "${path}" ]]; then
    printf '%s\n' "${label}: path missing: ${path}" >&2
    return 1
  fi

  src="$(mount_source "${path}")"
  if [[ -z "${src}" ]]; then
    printf '%s\n' "${label}: not a mountpoint: ${path}" >&2
    return 1
  fi

  if is_os_root_source "$(df_source "${path}")"; then
    printf '%s\n' "${label}: bound to OS root filesystem (${src}) — stale stub mount" >&2
    return 1
  fi

  total="$(df_kib_total "${path}")"
  if [[ -z "${total}" || "${total}" -lt "${min_kib}" ]]; then
    printf '%s\n' "${label}: size ${total:-0} KiB below floor ${min_kib} KiB (${path})" >&2
    return 1
  fi

  return 0
}

verify_host_media_mounts() {
  local root="${1:-}"
  local hdd nvme
  hdd="${MEDIA_HDD_PATH:-}"
  nvme="${MEDIA_NVME_PATH:-}"
  if [[ -z "${hdd}" && -n "${root}" ]]; then
    hdd="$(_env_from_dotenv MEDIA_HDD_PATH "${root}")"
  fi
  if [[ -z "${nvme}" && -n "${root}" ]]; then
    nvme="$(_env_from_dotenv MEDIA_NVME_PATH "${root}")"
  fi
  hdd="${hdd:-/mnt/media-hdd}"
  nvme="${nvme:-/mnt/media-nvme}"

  verify_host_media_path "${hdd}" "${MEDIA_HDD_MIN_KIB}" "hdd" || return 1
  verify_host_media_path "${nvme}" "${MEDIA_NVME_MIN_KIB}" "nvme" || return 1
  return 0
}

# Check a path inside a running container is not the OS root LV.
verify_container_media_path() {
  local container="$1"
  local path="$2"
  local src

  if ! docker inspect -f '{{.State.Running}}' "${container}" 2>/dev/null | grep -qx true; then
    printf '%s\n' "container ${container}: not running" >&2
    return 1
  fi

  src="$(docker exec "${container}" df -Pk "${path}" 2>/dev/null | awk 'NR==2 {print $1; exit}' || true)"
  if [[ -z "${src}" ]]; then
    printf '%s\n' "container ${container}: cannot df ${path}" >&2
    return 1
  fi
  if is_os_root_source "${src}"; then
    printf '%s\n' "container ${container}: ${path} on OS root (${src}) — stale_media_bind" >&2
    return 1
  fi
  return 0
}

emit_json_summary() {
  local root="${1:-}"
  local hdd nvme hdd_ok=false nvme_ok=false
  hdd="${MEDIA_HDD_PATH:-}"
  nvme="${MEDIA_NVME_PATH:-}"
  if [[ -z "${hdd}" && -n "${root}" ]]; then
    hdd="$(_env_from_dotenv MEDIA_HDD_PATH "${root}")"
  fi
  if [[ -z "${nvme}" && -n "${root}" ]]; then
    nvme="$(_env_from_dotenv MEDIA_NVME_PATH "${root}")"
  fi
  hdd="${hdd:-/mnt/media-hdd}"
  nvme="${nvme:-/mnt/media-nvme}"

  if verify_host_media_path "${hdd}" "${MEDIA_HDD_MIN_KIB}" "hdd" 2>/dev/null; then
    hdd_ok=true
  fi
  if verify_host_media_path "${nvme}" "${MEDIA_NVME_MIN_KIB}" "nvme" 2>/dev/null; then
    nvme_ok=true
  fi

  printf '{"hdd_path":"%s","hdd_ok":%s,"hdd_source":"%s","nvme_path":"%s","nvme_ok":%s,"nvme_source":"%s"}\n' \
    "${hdd}" "${hdd_ok}" "$(mount_source "${hdd}")" \
    "${nvme}" "${nvme_ok}" "$(mount_source "${nvme}")"
}

# --- CLI when executed (not sourced) ---
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  case "${1:-}" in
    --json)
      emit_json_summary "${ROOT}"
      verify_host_media_mounts "${ROOT}"
      ;;
    --container)
      verify_container_media_path "${2:?container name}" "${3:?path}"
      ;;
    ""|--)
      verify_host_media_mounts "${ROOT}"
      printf 'media mounts ok\n'
      ;;
    -h|--help)
      sed -n '1,12p' "$0"
      exit 0
      ;;
    *)
      printf 'usage: %s [--json | --container NAME PATH]\n' "$0" >&2
      exit 2
      ;;
  esac
fi
