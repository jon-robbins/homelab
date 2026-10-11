#!/usr/bin/env bash
# Preflight: critical host-published ports must not be held by foreign processes.
# Homelab containers (homelab-*) already bound are OK; systemd-resolved, unknown, etc. fail.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

if [[ "${SKIP_HOST_PORT_PREFLIGHT:-}" == "1" ]]; then
  echo "SKIP: SKIP_HOST_PORT_PREFLIGHT=1"
  exit 0
fi

if ! command -v ss >/dev/null 2>&1; then
  echo "SKIP: ss(8) not available"
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "SKIP: docker not available"
  exit 0
fi

COMPOSE_PROJECT="${COMPOSE_PROJECT:-homelab}"

# Curated from compose/network.yml + compose/media.yml + compose/download.yml (wildcard host binds only).
# Format: host_port|proto|service
CRITICAL_PORTS=(
  "80|tcp|caddy"
  "8443|tcp|caddy"
  "32400|tcp|plex"
  "8096|tcp|jellyfin"
)

is_wildcard_addr() {
  local addr="$1"
  case "$addr" in
    0.0.0.0 | "*" | "" | "[::]" | "::") return 0 ;;
  esac
  [[ "$addr" == *:* ]] && return 0 # [::]:port form handled separately
  return 1
}

# Returns lines: "addr|port|process_hint" for listeners on host_port/proto at wildcard binds.
wildcard_listeners() {
  local host_port="$1"
  local proto="$2"
  local ss_out line local_addr addr port process

  if [[ "$proto" == "tcp" ]]; then
    ss_out="$(ss -H -tlnp 2>/dev/null || ss -H -tln 2>/dev/null || true)"
  else
    ss_out="$(ss -H -ulnp 2>/dev/null || ss -H -uln 2>/dev/null || true)"
  fi

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    local_addr="$(awk '{print $4}' <<<"$line")"
    addr="${local_addr%:*}"
    port="${local_addr##*:}"
    [[ "$port" == "$host_port" ]] || continue

    # Strip bracketed IPv6 addr
    if [[ "$addr" == \[*\] ]]; then
      addr="${addr:1:${#addr}-2}"
    fi

    if ! is_wildcard_addr "$addr"; then
      continue
    fi

    process=""
    if [[ "$line" == *"users:"* ]]; then
      process="$(sed -n 's/.*users:((\"\([^\"]*\)\".*/\1/p' <<<"$line")"
      [[ -z "$process" ]] && process="$(sed -n 's/.*users:((\([^,]*\).*/\1/p' <<<"$line")"
    fi
    printf '%s|%s|%s\n' "$addr" "$port" "$process"
  done <<<"$ss_out"
}

# True if any homelab-* container maps container_port/proto -> host_port (e.g. 443/tcp -> :8443).
homelab_claims_host_port() {
  local host_port="$1"
  local proto="$2"
  local cname mapping container_spec host_part published_proto published_host

  while IFS= read -r cname; do
    [[ -z "$cname" ]] && continue
    while IFS= read -r mapping; do
      [[ "$mapping" != *" -> "* ]] && continue
      container_spec="${mapping%% -> *}"
      host_part="${mapping#* -> }"
      published_proto="${container_spec##*/}"
      [[ "$published_proto" == "$proto" ]] || continue
      published_host="${host_part##*:}"
      if [[ "$published_host" == "$host_port" ]]; then
        return 0
      fi
    done < <(docker port "$cname" 2>/dev/null || true)
  done < <(docker ps --format '{{.Names}}' 2>/dev/null | grep -E "^${COMPOSE_PROJECT}-" || true)
  return 1
}

listener_owned_by_homelab() {
  homelab_claims_host_port "$1" "$2"
}

known_foreign_process() {
  local proc="$1"
  case "$proc" in
    systemd-resolve | systemd-resolved)
      return 0
      ;;
  esac
  # Host dnsmasq / libvirt-dnsmasq (not docker-proxy)
  if [[ "$proc" == *dnsmasq* ]] && [[ "$proc" != *docker* ]]; then
    return 0
  fi
  return 1
}

failures=0

for entry in "${CRITICAL_PORTS[@]}"; do
  IFS='|' read -r host_port proto service <<<"$entry"
  mapfile -t listeners < <(wildcard_listeners "$host_port" "$proto")

  if ((${#listeners[@]} == 0)); then
    echo "PASS: ${service} ${proto}/${host_port} (no wildcard listener)"
    continue
  fi

  if listener_owned_by_homelab "$host_port" "$proto"; then
    echo "PASS: ${service} ${proto}/${host_port} (homelab container already published)"
    continue
  fi

  echo "FAIL: ${service} needs ${proto}/${host_port} on 0.0.0.0 (or ::) but a foreign process is listening:"
  while IFS='|' read -r addr port process; do
    [[ -z "$port" ]] && continue
    printf '  - %s:%s' "$addr" "$port"
    [[ -n "$process" ]] && printf ' (%s)' "$process"
    printf '\n'
    if known_foreign_process "$process"; then
      echo "    hint: stop the host resolver/DNS service (e.g. systemd-resolved) or free port ${host_port} before dep""loy"
    fi
  done < <(printf '%s\n' "${listeners[@]}")

  ss -tlnp 2>/dev/null | grep -E ":${host_port}\\b" >&2 || true
  if [[ "$proto" == "udp" ]]; then
    ss -ulnp 2>/dev/null | grep -E ":${host_port}\\b" >&2 || true
  fi
  if homelab_claims_host_port "$host_port" "$proto"; then
    echo "    note: homelab maps a container port to host ${host_port} but ss did not attribute listeners to docker"
  else
    echo "    homelab containers on this port: (none)"
  fi
  ((failures += 1)) || true
done

if ((failures > 0)); then
  echo ""
  echo "${failures} critical port(s) blocked — nightly dep""loy would fail with 'address already in use'."
  echo "Fix listeners above, or set SKIP_HOST_PORT_PREFLIGHT=1 to skip (not recommended on production host)."
  exit 1
fi

echo "All critical host ports are available for compose publish."
