#!/usr/bin/env bash
set -euo pipefail

[[ ${1:-} == --legacy-data ]] || exit 1
legacy=$2
shift 2
data="${ROOT:-}/srv/homelab/data"
rollback=0
specs=()
for arg; do
  if [[ $arg == --rollback ]]; then rollback=1; else specs+=("$arg"); fi
done

for spec in "${specs[@]}"; do
  svc=${spec%%=*}
  path=$svc
  [[ $spec == *=* ]] && path=${spec#*=}
  src=$legacy/$path
  dst=$data/$svc
  if (( rollback )); then
    [[ ! -e $src ]] || { echo "source exists: $src" >&2; exit 1; }
  else
    [[ ! -e $dst ]] || { echo "destination exists: $dst" >&2; exit 1; }
    [[ -e $src ]] || { echo "source missing: $src" >&2; exit 1; }
    [[ $(stat -c %d "$src") == "$(stat -c %d "$data")" ]] || {
      echo "cross-filesystem move: $src" >&2; exit 1;
    }
  fi
done

for spec in "${specs[@]}"; do
  svc=${spec%%=*}
  path=$svc
  [[ $spec == *=* ]] && path=${spec#*=}
  src=$legacy/$path
  dst=$data/$svc
  if (( rollback )); then
    target=$dst
    [[ -f $dst/$(basename "$src") ]] && target=$dst/$(basename "$src")
    mv "$target" "$src"
  else
    mapfile -t ids < <(docker ps -q --filter label=project=homelab --filter "label=service=$svc")
    ((${#ids[@]} == 0)) || docker stop "${ids[@]}"
    if [[ -d $src ]]; then mv "$src" "$dst"; else mkdir "$dst"; mv "$src" "$dst/"; fi
  fi
done
