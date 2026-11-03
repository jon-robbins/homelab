"""Real Docker: Authentik from an empty database to a proven gate, through the engine (nox session `wizard_e2e`).

Brings up and removes the "homelab" and "homelab-edge" projects and homelab_net, so it refuses to run against a
daemon that already has any of them. Downloads the pinned Authentik, Postgres and Caddy images.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from setup import cli
from setup.engine.checkpoints import Checkpoints
from setup.engine.contract import Context, Secret, Status
from setup.engine.layout import Layout
from setup.engine.local import Release, build_steps
from setup.engine.runner import run, status
from setup.engine.secrets import SecretStore
from setup.wiring.authentik import (
    GOOGLE_SECRET,
    Applied,
    Blueprints,
    FirstBoot,
    Person,
    Secrets,
    SetupClosed,
)
from setup.wiring.gate import Gate
from setup.wiring.stack import Stack

ROOT = Path(__file__).parents[2]
CONFIG = ROOT / "setup/config/authentik"
HOST = "127.0.0.1"
UID, GID = (os.getuid(), os.getgid()) if os.getuid() else (1000, 1000)
URLS = {
    "app_url": "https://app.example.test",
    "auth_url": "https://auth.example.test",
    "jellyfin_url": "https://jf.example.test",
}
CADDYFILE = """{
	auto_https disable_redirects
}
%s
https://auth.example.test:4443 {
	tls internal
	reverse_proxy authentik-server:9000
}
https://app.example.test:4443 {
	tls internal
	import strip_identity
	route {
		import authentik_gate
		import gate_probe
		handle {
			respond "Not Found" 404
		}
	}
}
"""


def docker(*argv: str) -> str:
    return subprocess.run(["docker", *argv], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def world(tmp_path):
    if any(
        docker("ps", "-aq", "--filter", f"label=com.docker.compose.project={p}") for p in ("homelab", "homelab-edge")
    ) or docker("network", "ls", "-q", "--filter", "name=^homelab_net$"):
        pytest.fail("this Docker daemon already has a 'homelab' project or homelab_net; refusing to run")
    host, site = tmp_path / "host", tmp_path / "site"
    for name in ("data", "secrets"):
        (host / name).mkdir(parents=True)
    (site / "overrides").mkdir(parents=True)
    shutil.copy(CONFIG / "override.authentik-worker.yml", site / "overrides/authentik-worker.yml")
    (site / "edge").mkdir()
    (site / "edge/Caddyfile").write_text(CADDYFILE % (CONFIG / "gate.caddy").read_text())
    services = ["authentik-postgres", "authentik-server", "authentik-worker", "caddy"]
    (site / "site.yaml").write_text(
        "contract: 1\nsite_id: e2e\nplugins: {}\nbind_roots: [APP_DATA_DIR]\nmarker_roots: []\n"
        f"services: {json.dumps({s: {} for s in services})}\nmanaged: {json.dumps(services)}\n"
    )
    (site / "site.env").write_text(
        f"PUID={UID}\nPGID={GID}\nTZ=UTC\nAPP_DATA_DIR={host}/data\nHOMELAB_SECRET_DIR={host}/secrets\n"
        f"BASE_DOMAIN=example.test\nTAILSCALE_IP={HOST}\n"
    )
    layout = Layout(tmp_path / "srv", owner_root=tmp_path / "owner", unit_dir=tmp_path / "units")
    layout.claim()
    ops = cli.LocalOps()
    ops.create_network("homelab_net")
    # The macOS gate runs inside Docker. Join only this test's network so its HTTP probes can reach Caddy.
    runner = os.uname().nodename if Path("/.dockerenv").exists() else None
    if runner:
        docker("network", "connect", "homelab_net", runner)
    store = SecretStore(host / "secrets")
    store.put("CLOUDFLARE_TOKEN", "unused-synthetic-token")
    ctx = Context(layout, store, Checkpoints(layout.setup_dir), ROOT, site)
    yield ctx
    for project, filename in (("homelab-edge", "edge.json"), ("homelab", "app.json")):
        for model in (tmp_path / "srv/setup/releases").glob("*/" + filename):
            subprocess.run(["docker", "compose", "-p", project, "-f", str(model), "down", "-v", "--remove-orphans"])
    if runner:
        docker("network", "disconnect", "homelab_net", runner)
    subprocess.run(["docker", "network", "rm", "homelab_net"], capture_output=True)


def steps_for(ctx: Context):
    release = Release.render(ctx.layout, ROOT, ctx.site, {})
    # Keep the catalog container definition; publish only this synthetic private listener.
    release.edge["services"]["caddy"]["ports"] = [
        {"target": 4443, "published": "18443", "host_ip": HOST, "protocol": "tcp"}
    ]
    # The pinned catalog image still carries a NET_BIND_SERVICE file capability (register row CF-12), so with
    # `cap_drop: [ALL]` its binary cannot exec ("operation not permitted"). Restore only that capability here; the
    # catalog module and the capability allowlist are unchanged until the image is rebuilt without it.
    release.edge["services"]["caddy"]["cap_add"] = ["NET_BIND_SERVICE"]
    release = Release.from_models(ctx.layout, release.app, release.edge, release.site)
    stack = Stack(release.directory / "app.json")
    people = [Person("owner", "owner@example.test", "Owner", admin=True), Person("kid", "kid@example.test", "Kid")]
    address = ("caddy", 4443) if Path("/.dockerenv").exists() else (HOST, 18443)
    gate = Gate(
        stack, release, address, URLS["app_url"], URLS["auth_url"], paths=("/sonarr", "/radarr"), verify_tls=False
    )
    extra = [
        Secrets(),
        FirstBoot(stack),
        Blueprints(release, URLS, people),
        Applied(stack, release),
        SetupClosed(address, URLS["auth_url"], verify_tls=False),
        gate,
    ]
    return build_steps(ctx, release, cli.LocalOps(settle_s=2), extra=extra), stack


def fake_google(prompt):
    assert isinstance(prompt, Secret)
    return "fake-" + secrets.token_urlsafe(24)  # not a credential


def test_empty_database_to_a_proven_gate_then_nothing_changes_and_rotation_converges(world):
    ctx = world
    steps, stack = steps_for(ctx)
    assert run(steps, ctx, ask=fake_google).ok
    before = docker("ps", "-aq", "--filter", "label=com.docker.compose.project=homelab")
    secret_values = [ctx.secrets.read(ctx.secrets.handle(n)) for n in ("AUTHENTIK_ADMIN_PASSWORD", GOOGLE_SECRET)]
    for container in before.split():
        assert not any(v in docker("inspect", container) for v in secret_values)  # nothing secret in the environment
    steps, stack = steps_for(ctx)
    remaining = {k: s for k, s in status(steps, ctx).items() if s.status is not Status.DONE}
    assert not remaining, remaining
    assert run(steps, ctx).ok
    assert docker("ps", "-aq", "--filter", "label=com.docker.compose.project=homelab") == before

    ctx.secrets.put(GOOGLE_SECRET, "rotated-" + secrets.token_urlsafe(24))
    assert status(steps, ctx)["AK-3"].status is Status.NEEDED
    assert run(steps, ctx).ok
    remaining = {k: s for k, s in status(steps, ctx).items() if s.status is not Status.DONE}
    assert not remaining, remaining
