#!/usr/bin/env bash
set -euo pipefail

sha=
replace=()
while (($#)); do
  case $1 in
    --sha) sha=${2:?missing SHA}; shift 2 ;;
    --replace) replace+=("${2:?missing unit}"); shift 2 ;;
    *) echo "usage: $0 --sha SHA [--replace UNIT]..." >&2; exit 2 ;;
  esac
done
[[ -n $sha ]] || { echo "usage: $0 --sha SHA [--replace UNIT]..." >&2; exit 2; }

repo=$(cd "$(dirname "$0")/.." && pwd)
[[ "$(git -C "$repo" rev-parse HEAD)" == "$sha" ]] || { echo "SHA does not match HEAD" >&2; exit 1; }
d=${DESTDIR:-}
h=$d/srv/homelab
t=$h/tooling/$sha
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/etc/systemd/system/docker.service.d" "$tmp/etc/sysctl.d"
printf '%s\n' \
  '[Unit]' 'After=docker.service' '' '[Service]' \
  'Type=oneshot' 'ExecStart=/srv/homelab/tooling/current/bin/homelabctl reconcile' \
  'Environment=DOCKER_CONFIG=/srv/homelab/trust/docker' >"$tmp/controller.service"
printf '%s\n' \
  '[Timer]' 'OnBootSec=2min' \
  'OnUnitActiveSec=5min' '' '[Install]' 'WantedBy=timers.target' >"$tmp/controller.timer"
printf '%s\n' \
  '[Unit]' 'After=docker.service' '' '[Service]' \
  'Type=oneshot' 'ExecStart=/srv/homelab/tooling/current/host/homelab-backup' \
  'EnvironmentFile=-/etc/homelab-backup/env' 'Nice=10' 'IOSchedulingClass=idle' >"$tmp/backup.service"
printf '%s\n' \
  '[Timer]' \
  'OnCalendar=*-*-* 04:15:00' 'Persistent=true' 'RandomizedDelaySec=10m' '' \
  '[Install]' 'WantedBy=timers.target' >"$tmp/backup.timer"
printf '%s\n' '[Unit]' 'After=tailscaled.service' >"$tmp/dropin"
printf '%s\n' 'net.ipv4.ip_nonlocal_bind=1' >"$tmp/sysctl"
printf '%s\n' \
  /etc/systemd/system/docker.service.d/10-homelab.conf \
  /etc/systemd/system/homelab-backup.service \
  /etc/systemd/system/homelab-backup.timer \
  /etc/systemd/system/homelab-controller.service \
  /etc/systemd/system/homelab-controller.timer \
  /etc/sysctl.d/90-homelab.conf \
  /var/lib/tailscale >"$tmp/HOST-FILES"

files=(
  "$d/etc/systemd/system/homelab-controller.service:$tmp/controller.service"
  "$d/etc/systemd/system/homelab-controller.timer:$tmp/controller.timer"
  "$d/etc/systemd/system/homelab-backup.service:$tmp/backup.service"
  "$d/etc/systemd/system/homelab-backup.timer:$tmp/backup.timer"
  "$d/etc/systemd/system/docker.service.d/10-homelab.conf:$tmp/dropin"
  "$d/etc/sysctl.d/90-homelab.conf:$tmp/sysctl"
  "$h/HOST-FILES:$tmp/HOST-FILES"
)
bad=0
for pair in "${files[@]}"; do
  path=${pair%%:*}; wanted=${pair#*:}; base=${path##*/}
  if [[ -e $path ]] && ! cmp -s "$path" "$wanted"; then
    allowed=0
    for r in "${replace[@]}"; do [[ $base == "$r"* ]] && allowed=1; done
    if (( ! allowed )); then
      diff -u "$path" "$wanted" || true
      bad=1
    fi
  fi
done
(( ! bad )) || exit 1

old=
if [[ -L $h/tooling/current ]]; then old=$(readlink "$h/tooling/current"); fi
previous_target=
if [[ -L $h/tooling/previous ]]; then previous_target=$(readlink "$h/tooling/previous"); fi
new_state=0
[[ -e $h/state ]] || new_state=1
changed=0
mkdir -p "$h" "$h/tooling" "$h/trust" "$h/state" "$h/releases" "$h/data"
chmod 700 "$h/trust"
if [[ ! -e $t ]]; then
  changed=1
  mkdir -p "$t"
  for dir in scripts host schemas config; do cp -a "$repo/$dir" "$t/$dir"; done
  python3 -m venv "$t/venv"
  pip=${HOMELAB_PIP:-$t/venv/bin/pip}
  "$pip" install --require-hashes -r "$t/config/release/requirements.txt"
  mkdir -p "$t/bin"
  printf '%s\n' '#!/usr/bin/env bash' "cd \"\$(dirname \"\$0\")/..\" && exec venv/bin/python -m scripts.controller.homelabctl \"\$@\"" >"$t/bin/homelabctl"
  chmod 755 "$t/bin/homelabctl"
fi
if [[ -n $old && $old != "$sha" ]]; then ln -sfn "$old" "$h/tooling/previous"; fi
ln -sfn "$sha" "$h/tooling/current"
find "$h/tooling" -mindepth 1 -maxdepth 1 -type d ! -name "$sha" ! -name "$old" ! -name "$previous_target" -exec rm -rf -- {} +
if (( new_state )); then touch "$h/state/observe-only"; changed=1; fi

for pair in "${files[@]}"; do
  path=${pair%%:*}; wanted=${pair#*:}
  if [[ ! -e $path ]] || ! cmp -s "$path" "$wanted"; then install -D -m 0644 "$wanted" "$path"; changed=1; fi
done
if [[ -z ${DESTDIR:-} ]]; then
  systemctl daemon-reload
  systemctl enable --now homelab-controller.timer
  systemctl enable homelab-backup.timer
  sysctl --load /etc/sysctl.d/90-homelab.conf
fi
(( changed )) && echo "installed" || echo "no changes"
