#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

mkdir -p "$TMP/bin" "$TMP/srv/homelab/data" "$TMP/legacy"
cat >"$TMP/bin/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$MOVE_DOCKER_LOG"
[[ $1 == ps ]] && exit 0
EOF
chmod +x "$TMP/bin/docker"

mkdir "$TMP/legacy/dir-svc"
mkdir -p "$TMP/legacy/homelab-mcp"
printf state >"$TMP/legacy/homelab-mcp/arr-post-import-state.json"
MOVE_DOCKER_LOG="$TMP/docker.log" ROOT="$TMP" PATH="$TMP/bin:$PATH" \
  "$ROOT_DIR/host/move-data.sh" --legacy-data "$TMP/legacy" \
  dir-svc homelab-arr-post-import=homelab-mcp/arr-post-import-state.json
[[ -d "$TMP/srv/homelab/data/dir-svc" && -d "$TMP/legacy/dir-svc" ]] || fail "directory was not copied"
[[ -f "$TMP/srv/homelab/data/homelab-arr-post-import/arr-post-import-state.json" ]] || fail "file was not mapped"
[[ -f "$TMP/legacy/homelab-mcp/arr-post-import-state.json" ]] || fail "source file was changed"
pass "directory and file moves"

mkdir "$TMP/legacy/keep" "$TMP/legacy/other"
touch "$TMP/srv/homelab/data/keep"
if ROOT="$TMP" PATH="$TMP/bin:$PATH" "$ROOT_DIR/host/move-data.sh" --legacy-data "$TMP/legacy" keep other; then fail "existing destination accepted"; fi
[[ -d "$TMP/legacy/other" ]] || fail "other argument moved after refusal"
pass "preflight destination refusal"

ROOT="$TMP" PATH="$TMP/bin:$PATH" "$ROOT_DIR/host/move-data.sh" --legacy-data "$TMP/legacy" \
  dir-svc homelab-arr-post-import=homelab-mcp/arr-post-import-state.json --rollback
[[ -d "$TMP/legacy/dir-svc" && -f "$TMP/legacy/homelab-mcp/arr-post-import-state.json" ]] || fail "rollback changed source"
[[ ! -e "$TMP/srv/homelab/data/dir-svc" ]] || fail "rollback left destination"
pass "rollback and no docker start"
