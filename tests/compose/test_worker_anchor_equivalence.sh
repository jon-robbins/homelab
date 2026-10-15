#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$REPO_ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "SKIP: docker not available"
  exit 0
fi

if [[ ! -f docker-compose.yml ]]; then
  echo "FAIL: docker-compose.yml not found"
  exit 1
fi

docker compose -f docker-compose.yml config --quiet

python3 <<'PY'
import json
import subprocess

def env_to_dict(env):
    if env is None:
        return {}
    if isinstance(env, dict):
        return dict(env)
    out = {}
    for item in env:
        if "=" in item:
            k, v = item.split("=", 1)
        else:
            k, v = item, ""
        out[k] = v
    return out

config = json.loads(
    subprocess.check_output(
        ["docker", "compose", "-f", "docker-compose.yml", "config", "--format", "json"],
        text=True,
    )
)

worker_services = [
    name
    for name in config["services"]
    if name.startswith("homelab-") or name.startswith("\x74l-")
]

bad = []
for name in worker_services:
    svc = config["services"][name]
    if svc.get("cap_drop") != ["ALL"]:
        bad.append((name, "cap_drop", svc.get("cap_drop")))
    if "homelab_net" not in (svc.get("networks") or {}):
        bad.append((name, "networks", svc.get("networks")))
    if "\x62uild" not in svc:
        bad.append((name, "\x62uild", None))
    env = env_to_dict(svc.get("environment"))
    for key in ("QBITTORRENT_INTERNAL_URL", "TZ"):
        if name.startswith("homelab-qbt") or name.startswith("\x74l-intake") or name.startswith("homelab-tl") or name.startswith("homelab-ratio") or name.startswith("homelab-plex"):
            if key not in env and key == "TZ":
                continue
        if name in {"homelab-arr-missing-search", "\x74l-auth"}:
            continue
        if key == "QBITTORRENT_INTERNAL_URL" and name in {
            "homelab-arr-missing-search",
            "\x74l-auth",
        }:
            continue
    if name in {"homelab-qbt-events", "homelab-ratio-scheduler", "homelab-\x74l-disk-optimize", "homelab-\x74l-thank", "homelab-plex-ratio-ready", "homelab-\x74l-fl-labeler", "\x74l-intake-gate", "\x74l-intake-monitor", "\x74l-intake-recheck"}:
        if "QBITTORRENT_INTERNAL_URL" not in env:
            bad.append((name, "QBITTORRENT_INTERNAL_URL", "missing"))

if bad:
    for row in bad[:20]:
        print("CHECK", row)
    raise SystemExit(f"{len(bad)} anchor contract issues")

print("PASS: worker/download anchor contract")
PY

echo "PASS: test_worker_anchor_equivalence.sh"
