#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=${ROOT:-}; DRY=0; ROLLBACK=0; CONFIRM=0; LEGACY=; MEDIA=()
while (($#)); do
  case $1 in
    --legacy-root) LEGACY=${2:?missing root}; shift 2;; --media-root) MEDIA+=("${2:?missing root}"); shift 2;;
    --dry-run) DRY=1; shift;; --rollback) ROLLBACK=1; shift;; --confirm) CONFIRM=1; shift;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done
P() { printf '%s%s' "$ROOT" "$1"; }
SCRIPT=$(realpath "$0"); LEGACY_ARG=$LEGACY; LEGACY=$(P "$LEGACY")
STATE=$(P /srv/homelab/state/cutover); TS_DIR=$(P /var/lib/tailscale); CONFIG=$(P /etc/infisical-agent/config.yaml)
DROPIN=$(P /etc/systemd/system/homelab-infisical-agent.service.d/10-homelab.conf); HOST_FILES=$(P /srv/homelab/HOST-FILES); PY=$(P /srv/homelab/tooling/current/venv/bin/python)
run() { if ((DRY)); then printf '[dry-run]'; printf ' %q' "$@"; echo; else "$@"; fi; }

rollback() {
  local bad=0 p src
  if systemctl stop homelab-cutover-deadman.timer 2>/dev/null; then :; fi
  for p in "${MEDIA[@]}"; do [[ -e "$(P "$p")/.homelab-mounted" ]] || continue; if ! media_action "$p" -i rm; then echo "rollback failed: media $p"; bad=1; fi; done
  if [[ -f $STATE/infisical-config.yaml.bak ]]; then
    if ! cp -a "$STATE/infisical-config.yaml.bak" "$CONFIG"; then echo 'rollback failed: restore infisical config'; bad=1; fi
    if ! rm -f "$DROPIN"; then echo 'rollback failed: remove infisical drop-in'; bad=1; fi
    if ! systemctl daemon-reload; then echo 'rollback failed: daemon-reload'; bad=1; fi
    if ! systemctl restart homelab-infisical-agent; then echo 'rollback failed: restart infisical'; bad=1; fi
  fi
  if ! systemctl enable homelab-boot-orchestrator.service; then echo 'rollback failed: enable orchestrator'; bad=1; fi
  if [[ -f $STATE/tailscale-source ]]; then
    src=$(cat "$STATE/tailscale-source")
    if ! systemctl disable --now tailscaled; then echo 'rollback failed: disable tailscaled'; bad=1; fi
    if ! systemctl mask tailscaled; then echo 'rollback failed: mask tailscaled'; bad=1; fi
    if ! cp -a "$TS_DIR/." "$src/"; then echo 'rollback failed: restore tailscale state'; bad=1; fi
    if ! docker compose -p homelab -f "$LEGACY/docker-compose.yml" up -d --no-deps tailscale; then echo 'rollback failed: compose tailscale'; bad=1; fi
  fi
  return $bad
}
media_action() {
  local p=$1 hp t; hp=$(P "$p"); mountpoint -q "$hp" || { echo "skip: $p is not a mountpoint"; return; }
  if ((DRY)); then
    t=$(P /tmp/homelab-cutover.XXXXXX); run touch "$hp/.homelab-mounted" || return $?; run mktemp -d "$t" || return $?; run mount --bind "$(P /)" "$t" || return $?; run chattr "$2" "$t$p" || return $?; run umount "$t" || return $?; [[ $3 == rm ]] && run rm -f "$hp/.homelab-mounted"; return 0
  fi
  [[ $3 == rm ]] || run touch "$hp/.homelab-mounted" || return $?; t=$(mktemp -d "$(P /tmp/homelab-cutover.XXXXXX)") || return $?
  run mount --bind "$(P /)" "$t" || return $?; run chattr "$2" "$t$p" || return $?; run umount "$t" || return $?; if [[ $3 == rm ]]; then run rm -f "$hp/.homelab-mounted" || return $?; fi
  return 0
}
tailscale_cutover() {
  local c src_raw src json host oldip olddns nowip nowdns i running=0
  c=$(docker ps -q --filter label=com.docker.compose.project=homelab --filter label=com.docker.compose.service=tailscale); [[ -n $c ]] || { echo 'tailscale container not found; skipping'; return; }
  run mkdir -p "$STATE" "$TS_DIR"; run sh -c 'docker exec "$1" tailscale --socket=/tmp/tailscaled.sock status --json > "$2"' _ "$c" "$STATE/tailscale.json"
  if ((DRY)); then json=$(docker exec "$c" tailscale --socket=/tmp/tailscaled.sock status --json); else json=$(cat "$STATE/tailscale.json"); fi
  host=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["HostName"])' <<<"$json"); oldip=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["TailscaleIPs"][0])' <<<"$json"); olddns=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"])' <<<"$json")
  src_raw=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/tailscale"}}{{.Source}}{{end}}{{end}}' "$c"); src=$(P "$src_raw")
  run sh -c 'printf "%s\n" "$1" > "$2"' _ "$src" "$STATE/tailscale-source"; run docker stop "$c"; run cp -a "$src/." "$TS_DIR/"; run systemctl unmask tailscaled; run systemctl enable --now tailscaled
  if ((DRY)); then echo '[dry-run] wait for tailscale BackendState=Running'; else
    for ((i=0;i<60;i++)); do [[ $(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState"))') == Running ]] && { running=1; break; }; sleep 1; done
    ((running)) || { echo 'tailscale did not reach BackendState=Running' >&2; return 1; }
  fi
  run tailscale set "--hostname=$host"; if ((DRY)); then return 0; fi
  nowdns=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Self",{}).get("DNSName"))'); nowip=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Self",{}).get("TailscaleIPs",[""])[0])')
  [[ $nowip == "$oldip" && $nowdns == "$olddns" ]] || { echo 'tailscale identity mismatch' >&2; return 1; }; run docker rm "$c"
}
infisical_cutover() {
  local source=$LEGACY/config/infisical/templates target code; target=$(P /srv/homelab/infisical/templates)
  run mkdir -p "$(dirname "$CONFIG")" "$(dirname "$DROPIN")" "$target"; run cp -a "$source/." "$target/"; run cp -a "$CONFIG" "$STATE/infisical-config.yaml.bak"
  code='import sys,yaml; p=sys.argv[1]; d=yaml.safe_load(open(p)); f=lambda x: ({k:f(v) for k,v in x.items() if k != "commands"} if isinstance(x,dict) else [f(v) for v in x] if isinstance(x,list) else x.replace(sys.argv[2],sys.argv[3]) if isinstance(x,str) else x); open(p,"w").write(yaml.safe_dump(f(d),sort_keys=False))'
  run "$PY" -c "$code" "$CONFIG" "$source/" "$target/"; run sh -c "printf '%s\n' '[Service]' 'WorkingDirectory=/srv/homelab' > '$DROPIN'"; run systemctl daemon-reload; run systemctl restart homelab-infisical-agent
}
if ((CONFIRM)); then
  fail=0
  if [[ -f $STATE/tailscale.json ]]; then
    saved=$(cat "$STATE/tailscale.json")
    if ! now=$(tailscale status --json); then echo 'tailscale status failed'; fail=1
    else
      python3 -c 'import json,sys; a=json.loads(sys.argv[1]); b=json.loads(sys.argv[2]); assert b.get("BackendState") == "Running" and b["Self"]["TailscaleIPs"][0] and a["Self"]["TailscaleIPs"][0] == b["Self"]["TailscaleIPs"][0] and a["Self"]["DNSName"] == b["Self"]["DNSName"]' "$saved" "$now" || { echo 'tailscale identity check failed'; fail=1; }
    fi
  fi
  systemctl is-active homelab-infisical-agent >/dev/null || { echo 'infisical agent inactive'; fail=1; }; if (( ! fail )); then systemctl stop homelab-cutover-deadman.timer || { echo 'failed to stop homelab-cutover-deadman.timer'; exit 1; }; echo confirmed; fi; exit $fail
fi
if ((ROLLBACK)); then rollback; exit $?; fi
args=(--unit=homelab-cutover-deadman --on-active=10min "$SCRIPT" --legacy-root "$LEGACY_ARG"); for p in "${MEDIA[@]}"; do args+=(--media-root "$p"); done; args+=(--rollback); run systemd-run "${args[@]}"
# shellcheck disable=SC2154
trap 'rc=$?; trap - ERR; rollback || :; exit "$rc"' ERR
run mkdir -p "$STATE"; tailscale_cutover; run systemctl disable --now homelab-boot-orchestrator.service; infisical_cutover
for p in "${MEDIA[@]}"; do media_action "$p" +i keep; done
if policy=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' 3x-ui 2>/dev/null); then echo "3x-ui restart policy: ${policy:-}"; else echo '3x-ui: not found'; fi
if [[ ! -f $HOST_FILES ]] || ! grep -Fxq "$DROPIN" "$HOST_FILES"; then run sh -c "printf '%s\n' '$DROPIN' >> '$HOST_FILES'"; fi
echo 'cutover done; automatic rollback in 10 min unless confirmed.'; echo "From a new SSH session over Tailscale run: $SCRIPT --confirm"
