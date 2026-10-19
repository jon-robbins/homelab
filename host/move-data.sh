#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: move-data.sh --legacy-data DIR [--rollback] SVC[=PATH]..." >&2
  exit 2
}

[[ ${1:-} == --legacy-data && $# -ge 3 ]] || usage
legacy=$2
shift 2
data="${ROOT:-}/srv/homelab/data"
rollback=0
specs=()
for arg; do
  if [[ $arg == --rollback ]]; then rollback=1; else specs+=("$arg"); fi
done
((${#specs[@]})) || usage

sources=()
destinations=()
total=0
for spec in "${specs[@]}"; do
  svc=${spec%%=*}
  # rollback runs rm -rf $data/$svc: an empty or path-like name would reach outside one service's directory
  [[ -n $svc && $svc != *[/]* && $svc != .. && $svc != . ]] || { echo "bad service name: $spec" >&2; exit 1; }
  path=$svc
  [[ $spec == *=* ]] && path=${spec#*=}
  src=$legacy/$path
  dst=$data/$svc
  sources+=("$src")
  destinations+=("$dst")
  if (( rollback )); then
    [[ -e $src ]] || { echo "source missing: $src" >&2; exit 1; }
  else
    [[ ! -e $dst ]] || { echo "destination exists: $dst" >&2; exit 1; }
    [[ -e $src ]] || { echo "source missing: $src" >&2; exit 1; }
    total=$((total + $(du -sk "$src" | awk '{print $1}')))
  fi
done

if (( ! rollback )); then
  read -r _ size avail < <(df -Pk "$data" | awk 'NR == 2 {print $1, $2, $4}')
  need=$((total + size / 10))
  if (( total > avail - size / 10 )); then
    echo "insufficient space: need $need KiB" >&2
    exit 1
  fi
fi

for i in "${!specs[@]}"; do
  src=${sources[i]}
  dst=${destinations[i]}
  if (( rollback )); then
    rm -rf "$dst"
  else
    svc=${specs[i]%%=*}
    mapfile -t ids < <(docker ps -q \
      --filter label=com.docker.compose.project=homelab \
      --filter "label=com.docker.compose.service=$svc")
    ((${#ids[@]} == 0)) || docker stop "${ids[@]}"
    if [[ -d $src ]]; then cp -a "$src" "$dst"; else mkdir "$dst"; cp -a "$src" "$dst/"; fi
  fi
done
