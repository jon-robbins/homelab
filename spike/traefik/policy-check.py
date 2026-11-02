#!/usr/bin/env python3
"""Run the repo's real compose policy (scripts/release/compose_policy.py) on module/traefik.compose.yml.

Usage: policy-check.py <repo-root> [--allow-traefik-namespace]
The flag simulates adding `docker.io/library/traefik` to config/release/image-policy.json in memory (nothing on disk
changes), to show that the namespace is the only thing the policy objects to.
"""
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from scripts.release import compose_policy as cp  # noqa: E402
from scripts.release.contracts import ValidationErrors  # noqa: E402

env = {
    "PUID": "1000", "PGID": "1000", "BASE_DOMAIN": "example.homelab.local", "TAILSCALE_IP": "198.51.100.10",
    "APP_DATA_DIR": "/var/tmp/spike-data", "HOMELAB_SECRET_DIR": "/var/tmp/spike-secrets",
}
module = Path(__file__).parent / "module" / "traefik.compose.yml"
out = subprocess.run(
    ["docker", "compose", "-f", str(module), "config", "--format", "json"],
    check=True, capture_output=True, text=True, env={**__import__("os").environ, **env},
).stdout
model = json.loads(out)
if "--allow-traefik-namespace" in sys.argv:
    policy = cp._allowlist("image-policy.json")
    policy["namespaces"] = [*policy["namespaces"], "docker.io/library/traefik"]
ctx = cp.Context(tiers={"traefik": "edge", "gate-probe": "edge"}, namespaces={}, env=env, roots=(env["APP_DATA_DIR"],))
try:
    cp.check_policy(model, ctx)
    print("POLICY OK: no violations")
except ValidationErrors as error:
    print("POLICY VIOLATIONS:")
    for item in error.errors:
        print("  -", item)
    sys.exit(1)
