#!/usr/bin/env bash
# Move host duties off the legacy stack: Tailscale from its container to the host daemon, the Infisical agent onto
# /srv/homelab, the boot orchestrator off, and disk markers onto the media roots. A dead-man timer runs --rollback
# unless --confirm succeeds from a new SSH session over Tailscale within 10 minutes. The timer is an enabled unit, so a
# reboot before --confirm re-arms it for 10 minutes after boot.
set -euo pipefail

ROOT=${ROOT:-}
DRY=0
ROLLBACK=0
CONFIRM=0
LEGACY_ARG=
MEDIA=()
while (($#)); do
  case $1 in
    --legacy-root) LEGACY_ARG=${2:?missing root}; shift 2 ;;
    --media-root) MEDIA+=("${2:?missing root}"); shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --rollback) ROLLBACK=1; shift ;;
    --confirm) CONFIRM=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

P() { printf '%s%s' "$ROOT" "$1"; }
SCRIPT=$(realpath "$0")
LEGACY=$(P "$LEGACY_ARG")
STATE=$(P /srv/homelab/state/cutover)
TS_DIR=$(P /var/lib/tailscale)
CONFIG=$(P /etc/infisical-agent/config.yaml)
DROPIN=$(P /etc/systemd/system/homelab-infisical-agent.service.d/10-homelab.conf)
HOST_FILES=$(P /srv/homelab/HOST-FILES)
TOOLING=$(P /srv/homelab/tooling/current)
PY=$TOOLING/venv/bin/python
DEADMAN=homelab-cutover-deadman
UNITS=$(P /etc/systemd/system)

run() { if ((DRY)); then printf '[dry-run]'; printf ' %q' "$@"; echo; else "$@"; fi; }

# `tailscale status --json` on stdin → "BackendState|HostName|first Tailscale IP|DNSName"
ts_fields() {
  python3 -c 'import json, sys
status = json.load(sys.stdin)
me = status.get("Self") or {}
print(status.get("BackendState", ""), me.get("HostName", ""), (me.get("TailscaleIPs") or [""])[0],
      me.get("DNSName", ""), sep="|")'
}

# Flip the immutable bit (+i/-i) on the directory a media disk mounts over, reached through a bind mount of /
# so the bit lands on the root filesystem's directory, not on the mounted disk.
set_immutable() {
  local root=$1 flag=$2 view rc=0
  view=$(P /tmp/homelab-cutover.XXXXXX)
  if ((DRY)); then run mktemp -d "$view"; else view=$(mktemp -d "$view"); fi
  run mount --bind "$(P /)" "$view"
  run chattr "$flag" "$view$root" || rc=$?
  run umount "$view"
  return "$rc"
}

mark_media() {
  local root=$1
  mountpoint -q "$(P "$root")" || { echo "skip: $root is not a mountpoint"; return 0; }
  run touch "$(P "$root")/.homelab-mounted"
  set_immutable "$root" +i
}

unmark_media() {
  local root=$1
  [[ -e $(P "$root")/.homelab-mounted ]] || return 0
  set_immutable "$root" -i && run rm -f "$(P "$root")/.homelab-mounted"
}

tailscale_cutover() {
  local c status src host ip dns state now_ip now_dns i
  c=$(docker ps -q --filter label=com.docker.compose.project=homelab --filter label=com.docker.compose.service=tailscale)
  [[ -n $c ]] || { echo 'tailscale container not found; skipping'; return 0; }
  run mkdir -p "$STATE" "$TS_DIR"
  run sh -c 'docker exec "$1" tailscale --socket=/tmp/tailscaled.sock status --json > "$2"' _ "$c" "$STATE/tailscale.json"
  if ((DRY)); then
    status=$(docker exec "$c" tailscale --socket=/tmp/tailscaled.sock status --json)
  else
    status=$(cat "$STATE/tailscale.json")
  fi
  IFS='|' read -r _ host ip dns <<<"$(ts_fields <<<"$status")"
  [[ -n $host && -n $ip ]] || { echo 'cannot read the tailscale identity' >&2; return 1; }

  src=$(P "$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/tailscale"}}{{.Source}}{{end}}{{end}}' "$c")")
  run sh -c 'printf "%s\n" "$1" > "$2"' _ "$src" "$STATE/tailscale-source"
  run docker stop "$c"
  run cp -a "$src/." "$TS_DIR/"
  run systemctl unmask tailscaled
  run systemctl enable --now tailscaled
  # The container never managed host DNS. With it accepted, resolv.conf points only at MagicDNS, which has no
  # upstream for public names on this tailnet, so every public lookup fails.
  if ((DRY)); then
    echo '[dry-run] wait for tailscale BackendState=Running'
    run tailscale set "--hostname=$host" --accept-dns=false
    return 0
  fi

  for ((i = 0; i < 60; i++)); do
    status=$(tailscale status --json 2>/dev/null) || status='{}'
    IFS='|' read -r state _ <<<"$(ts_fields <<<"$status")"
    [[ $state == Running ]] && break
    sleep 1
  done
  [[ $state == Running ]] || { echo 'tailscale did not reach BackendState=Running' >&2; return 1; }
  run tailscale set "--hostname=$host" --accept-dns=false
  IFS='|' read -r _ _ now_ip now_dns <<<"$(tailscale status --json | ts_fields)"
  [[ $now_ip == "$ip" && $now_dns == "$dns" ]] || { echo 'tailscale identity mismatch' >&2; return 1; }
  run docker rm "$c"
}

infisical_cutover() {
  local source=$LEGACY/config/infisical/templates target templates
  target=$(P /srv/homelab/infisical/templates)
  # The agent config names the legacy templates directory even where that checkout no longer has it; the
  # release tooling ships the same templates.
  templates=$source
  [[ -d $templates ]] || templates=$TOOLING/config/infisical/templates
  run mkdir -p "$(dirname "$CONFIG")" "$(dirname "$DROPIN")" "$target"
  run cp -a "$templates/." "$target/"
  run cp -a "$CONFIG" "$STATE/infisical-config.yaml.bak"
  run "$PY" - "$CONFIG" "$source/" "$target/" <<'PY'
import sys

import yaml

path, old, new = sys.argv[1:]


def rewrite(node):
    """Drop the agent's reload commands (the controller restarts services) and repoint template paths."""
    if isinstance(node, dict):
        return {key: rewrite(value) for key, value in node.items() if key != "commands"}
    if isinstance(node, list):
        return [rewrite(value) for value in node]
    return node.replace(old, new) if isinstance(node, str) else node


with open(path) as handle:
    config = yaml.safe_load(handle)
with open(path, "w") as handle:
    yaml.safe_dump(rewrite(config), handle, sort_keys=False)
PY
  run sh -c 'printf "[Service]\nWorkingDirectory=/srv/homelab\n" > "$1"' _ "$DROPIN"
  run systemctl daemon-reload
  run systemctl restart homelab-infisical-agent
}

# The dead-man timer is a unit file rather than a transient systemd-run timer so that it survives a reboot.
arm_deadman() {
  local exec=("$SCRIPT" --legacy-root "$LEGACY_ARG") root
  for root in "${MEDIA[@]}"; do exec+=(--media-root "$root"); done
  exec+=(--rollback)
  run sh -c 'printf "%s\n" "$1" > "$2"' _ "[Unit]
Description=Roll back the homelab cutover
[Service]
Type=oneshot
ExecStart=${exec[*]}" "$UNITS/$DEADMAN.service"
  run sh -c 'printf "%s\n" "$1" > "$2"' _ "[Unit]
Description=Roll back the homelab cutover 10 min after it starts or after a boot, unless confirmed
[Timer]
OnActiveSec=10min
AccuracySec=1s
[Install]
WantedBy=timers.target" "$UNITS/$DEADMAN.timer"
  run systemctl daemon-reload
  run systemctl enable --now "$DEADMAN.timer" || { disarm_deadman 2>/dev/null || true; return 1; }
}

disarm_deadman() {
  run systemctl disable --now "$DEADMAN.timer" || return 1
  run rm -f "$UNITS/$DEADMAN.timer" "$UNITS/$DEADMAN.service"
  run systemctl daemon-reload
}

ROLLBACK_FAILED=0
undo() { run "$@" || { echo "rollback failed: $*"; ROLLBACK_FAILED=1; }; }

# Undo whatever the cutover got to, in reverse order. A failing step is reported and the remaining steps still run.
rollback() {
  local root src
  disarm_deadman 2>/dev/null || true
  for root in "${MEDIA[@]}"; do
    unmark_media "$root" || { echo "rollback failed: media $root"; ROLLBACK_FAILED=1; }
  done
  if [[ -f $STATE/infisical-config.yaml.bak ]]; then
    undo cp -a "$STATE/infisical-config.yaml.bak" "$CONFIG"
    undo rm -f "$DROPIN"
    undo systemctl daemon-reload
    undo systemctl restart homelab-infisical-agent
  fi
  undo systemctl enable homelab-boot-orchestrator.service
  if [[ -f $STATE/tailscale-source ]]; then
    src=$(cat "$STATE/tailscale-source")
    undo systemctl disable --now tailscaled
    undo systemctl mask tailscaled
    undo cp -a "$TS_DIR/." "$src/"
    undo docker compose -p homelab -f "$LEGACY/docker-compose.yml" up -d --no-deps tailscale
  fi
  return "$ROLLBACK_FAILED"
}

# Run by the owner from a new SSH session over Tailscale: that session is the real check; this adds what it can see.
confirm() {
  local ok=1 status saved_ip saved_dns state ip dns
  if [[ -f $STATE/tailscale.json ]]; then
    IFS='|' read -r _ _ saved_ip saved_dns <<<"$(ts_fields <"$STATE/tailscale.json")"
    if ! status=$(tailscale status --json); then
      echo 'tailscale status failed'; ok=0
    else
      IFS='|' read -r state _ ip dns <<<"$(ts_fields <<<"$status")"
      if [[ $state != Running || -z $ip || $ip != "$saved_ip" || $dns != "$saved_dns" ]]; then
        echo 'tailscale identity check failed'; ok=0
      fi
    fi
  fi
  systemctl is-active homelab-infisical-agent >/dev/null || { echo 'infisical agent inactive'; ok=0; }
  ((ok)) || return 1
  disarm_deadman || { echo "failed to stop $DEADMAN.timer"; return 1; }
  echo confirmed
}

if ((CONFIRM)); then confirm; exit; fi
if ((ROLLBACK)); then rollback; exit; fi

arm_deadman

# From here on, any failure rolls back before exiting.
on_exit() {
  local rc=$?
  trap - EXIT
  ((rc == 0)) || rollback || true
  exit "$rc"
}
trap on_exit EXIT
run mkdir -p "$STATE"
tailscale_cutover
run systemctl disable --now homelab-boot-orchestrator.service
infisical_cutover
for root in "${MEDIA[@]}"; do mark_media "$root"; done
if policy=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' 3x-ui 2>/dev/null); then
  echo "3x-ui restart policy: ${policy:-}"
else
  echo '3x-ui: not found'
fi
grep -Fxqs "$DROPIN" "$HOST_FILES" || run sh -c 'printf "%s\n" "$1" >> "$2"' _ "$DROPIN" "$HOST_FILES"
trap - EXIT

echo 'cutover done; automatic rollback in 10 min unless confirmed.'
echo "From a new SSH session over Tailscale run: $SCRIPT --confirm"
