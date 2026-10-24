#!/usr/bin/env bash
# Run homelab-mcp in Docker the way the host does (non-root, no capabilities,
# mutations on, real media folders) and drive it with an MCP client.
#
#   apps/homelab-mcp/scripts/docker-smoke.sh            # build and test the public image
#   MCP_IMAGE=other:tag SMOKE_NETWORK=net SMOKE_EXTRA=/path/checks.py docker-smoke.sh
#
# SMOKE_NETWORK joins an existing network (for stub upstreams); SMOKE_EXTRA adds
# client checks (see docker_smoke_client.py).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
scripts="$root/apps/homelab-mcp/scripts"
image="${MCP_IMAGE:-}"
if [[ -z "$image" ]]; then
  image="homelab-mcp:smoke"
  docker build -q -f "$root/apps/homelab-mcp/Dockerfile" -t "$image" "$root/apps" >/dev/null
fi

name="homelab-mcp-smoke-$$"
network="${SMOKE_NETWORK:-$name}"
work="$(mktemp -d)"
owned_network=""
[[ -n "${SMOKE_NETWORK:-}" ]] || owned_network="$network"
trap 'docker rm -f "$name" >/dev/null 2>&1; [[ -z "$owned_network" ]] || docker network rm "$owned_network" >/dev/null 2>&1; rm -rf "$work"' EXIT

mkdir -p "$work/downloads" "$work/movies" "$work/tv/Show/Season 01" "$work/data"
echo video >"$work/downloads/Film.mkv"
echo video >"$work/tv/Show/Season 01/ep1.mkv"
chmod -R a+rwX "$work"

[[ -z "$owned_network" ]] || docker network create "$network" >/dev/null
token="smoke-$$"
docker run -d --name "$name" --network "$network" \
  --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges:true \
  -e MCP_API_TOKEN="$token" -e HOMELAB_MCP_ENABLE_MUTATIONS=1 \
  -e HOMELAB_MCP_AUDIT_PATH=/data/audit.jsonl \
  -v "$work/downloads:/downloads" -v "$work/movies:/movies" -v "$work/tv:/tv" \
  -v "$work/data:/data" \
  "$image" >/dev/null

extra=()
if [[ -n "${SMOKE_EXTRA:-}" ]]; then
  extra=(-v "$SMOKE_EXTRA:/smoke/extra.py:ro" -e SMOKE_EXTRA=/smoke/extra.py)
fi
status=0
docker run --rm --network "$network" --user 1000:1000 \
  -v "$scripts/docker_smoke_client.py:/smoke/client.py:ro" "${extra[@]}" \
  --entrypoint python "$image" /smoke/client.py "http://$name:8090" "$token" || status=$?
if [[ $status -ne 0 ]]; then
  docker logs "$name" 2>&1 | tail -30
fi
exit "$status"
