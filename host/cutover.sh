#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-}"
DRY=0
ROLLBACK=0
LEGACY=
MEDIA=()
while (($#)); do
  case "$1" in
    --legacy-root) LEGACY="$2"; shift 2 ;;
    --media-root) MEDIA+=("$2"); shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --rollback) ROLLBACK=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

P() { printf '%s%s' "$ROOT" "$1"; }
LEGACY_ARG="$LEGACY"
LEGACY="$(P "$LEGACY")"
STATE="$(P /srv/homelab/state/cutover)"
TS_DIR="$(P /var/lib/tailscale)"
CONFIG="$(P /etc/infisical-agent/config.yaml)"
DROPIN="$(P /etc/systemd/system/homelab-infisical-agent.service.d/10-homelab.conf)"
HOST_FILES="$(P /srv/homelab/HOST-FILES)"
PY="$(P /srv/homelab/tooling/current/venv/bin/python)"
run() { if ((DRY)); then printf '[dry-run]'; printf ' %q' "$@"; echo; else "$@"; fi; }

media_action() {
  local p="$1" hp t
  hp="$(P "$p")"
  if ! mountpoint -q "$hp"; then echo "skip: $p is not a mountpoint"; return; fi
  if ((DRY)); then
    t="$(P /tmp/homelab-cutover.XXXXXX)"
    run touch "$hp/.homelab-mounted"
    run mktemp -d "$t"
    run mount --bind "$(P /)" "$t"
    run chattr "$2" "$t$p"
    run umount "$t"
    if [[ "$3" == rm ]]; then run rm -f "$hp/.homelab-mounted"; fi
    return
  fi
  [[ "$3" == rm ]] || run touch "$hp/.homelab-mounted"
  t="$(mktemp -d "$(P /tmp/homelab-cutover.XXXXXX)")"
  run mount --bind "$(P /)" "$t"
  run chattr "$2" "$t$p"
  run umount "$t"
  if [[ "$3" == rm ]]; then run rm -f "$hp/.homelab-mounted"; fi
}

tailscale_cutover() {
  local c src_raw src json host oldip olddns nowip nowdns i running=0
  c="$(docker ps -q --filter label=com.docker.compose.project=homelab --filter label=com.docker.compose.service=tailscale)"
  [[ -n "$c" ]] || { echo "tailscale container not found; skipping"; return; }
  run mkdir -p "$STATE" "$TS_DIR"
  run sh -c 'docker exec "$1" tailscale --socket=/tmp/tailscaled.sock status --json > "$2"' _ "$c" "$STATE/tailscale.json"
  if ((DRY)); then json="$(docker exec "$c" tailscale --socket=/tmp/tailscaled.sock status --json)"; else json="$(cat "$STATE/tailscale.json")"; fi
  host="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["HostName"])' <<<"$json")"
  oldip="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["TailscaleIPs"][0])' <<<"$json")"
  olddns="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"])' <<<"$json")"
  src_raw="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/tailscale"}}{{.Source}}{{end}}{{end}}' "$c")"
  src="$(P "$src_raw")"
  run sh -c 'printf "%s\n" "$1" > "$2"' _ "$src" "$STATE/tailscale-source"
  run docker stop "$c"; run cp -a "$src/." "$TS_DIR/"
  run systemctl unmask tailscaled; run systemctl enable --now tailscaled
  if ((DRY)); then echo "[dry-run] wait for tailscale BackendState=Running"; else
    for ((i=0;i<60;i++)); do
      if [[ "$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState"))')" == Running ]]; then running=1; break; fi
      sleep 1
    done
    ((running)) || { echo "tailscale did not reach BackendState=Running" >&2; exit 1; }
  fi
  run tailscale set "--hostname=$host"
  if ((DRY)); then return; fi
  nowdns="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Self",{}).get("DNSName"))')"
  nowip="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Self",{}).get("TailscaleIPs",[""])[0])')"
  if [[ "$nowip" != "$oldip" || "$nowdns" != "$olddns" ]]; then
    echo "tailscale identity mismatch; rollback with: $0 --legacy-root $LEGACY_ARG --rollback" >&2
    exit 1
  fi
  run docker rm "$c"
}

infisical_cutover() {
  local source="${LEGACY}/config/infisical/templates" target code
  target="$(P /srv/homelab/infisical/templates)"
  run mkdir -p "$(dirname "$CONFIG")" "$(dirname "$DROPIN")" "$target"
  run cp -a "$source/." "$target/"
  run cp -a "$CONFIG" "${STATE}/infisical-config.yaml.bak"
  code='import sys,yaml; p=sys.argv[1]; d=yaml.safe_load(open(p)); f=lambda x: ({k:f(v) for k,v in x.items() if k != "commands"} if isinstance(x,dict) else [f(v) for v in x] if isinstance(x,list) else x.replace(sys.argv[2],sys.argv[3]) if isinstance(x,str) else x); open(p,"w").write(yaml.safe_dump(f(d),sort_keys=False))'
  run "$PY" -c "$code" "$CONFIG" "$source/" "$target/"
  run sh -c "printf '%s\\n' '[Service]' 'WorkingDirectory=/srv/homelab' > '$DROPIN'"
  run systemctl daemon-reload; run systemctl restart homelab-infisical-agent
}

if ((ROLLBACK)); then
  for p in "${MEDIA[@]}"; do media_action "$p" -i rm; done
  if [[ -f "${STATE}/infisical-config.yaml.bak" ]]; then
    run cp -a "${STATE}/infisical-config.yaml.bak" "$CONFIG"; run rm -f "$DROPIN"; run systemctl daemon-reload; run systemctl restart homelab-infisical-agent
  fi
  run systemctl enable homelab-boot-orchestrator.service
  src="$(cat "${STATE}/tailscale-source" 2>/dev/null || true)"
  if [[ -n "$src" ]]; then run systemctl disable --now tailscaled; run systemctl mask tailscaled; run cp -a "$TS_DIR/." "$src/"; run docker compose -p homelab -f "$LEGACY/docker-compose.yml" up -d --no-deps tailscale; fi
  exit 0
fi

run mkdir -p "$STATE"
tailscale_cutover
run systemctl disable --now homelab-boot-orchestrator.service
infisical_cutover
for p in "${MEDIA[@]}"; do media_action "$p" +i keep; done
if policy="$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' 3x-ui 2>/dev/null)"; then echo "3x-ui restart policy: ${policy:-}"; else echo "3x-ui: not found"; fi
if [[ ! -f "$HOST_FILES" ]] || ! grep -Fxq "$DROPIN" "$HOST_FILES"; then run sh -c "printf '%s\\n' '$DROPIN' >> '$HOST_FILES'"; fi
