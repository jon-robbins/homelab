"""Policy v2 and the module rules, on models shaped like `docker compose config --format json` output.

The accept rows are production's real shapes (sonarr, jellyfin, plex, cloudflared, overseerr); each reject row
breaks exactly one rule.
"""

import os
from pathlib import Path

import pytest
import yaml

from scripts.release.compose_policy import Context, check_policy
from scripts.release.contracts import ValidationError
from scripts.release.modules import read_env

DIGEST = "@sha256:" + "a" * 64
SITE = Path(__file__).parent / "fixtures/site"
ENV = read_env(SITE / "site.env")
ROOTS = tuple(ENV[key] for key in yaml.safe_load((SITE / "site.yaml").read_text())["bind_roots"])


def bind(source, target="/x"):
    return {"type": "bind", "source": source, "target": target, "bind": {"create_host_path": False}}


def service(**extra):
    base = {
        "image": "lscr.io/linuxserver/sonarr:4.0.17" + DIGEST,
        "labels": {"homelab.state": "stateful"},
        "healthcheck": {"test": ["CMD-SHELL", "curl -fsS http://localhost:8989 || exit 1"]},
    }
    return {**base, **extra}


def check(services, *, tiers=None, namespaces=None, roots=ROOTS, secrets=None, env=ENV, exposed=()):
    model = {"services": services, **({"secrets": secrets} if secrets else {})}
    tiers = {name: "app" for name in services} if tiers is None else tiers
    ctx = Context(tiers=tiers, namespaces=namespaces or {}, env=env, roots=roots, exposed=frozenset(exposed))
    check_policy(model, ctx)


def test_production_shapes_pass():
    check(
        {
            "sonarr": service(
                cap_drop=["ALL"],
                cap_add=["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
                volumes=[bind(f"{ENV['APP_DATA_DIR']}/sonarr", "/config"), bind(ENV["TV_DIR"], "/tv")],
            ),
            "jellyfin": service(
                devices=[{"source": "/dev/dri", "target": "/dev/dri", "permissions": "rwm"}], gpus="all"
            ),
            "plex": service(gpus=[{"count": -1, "capabilities": ["gpu"]}], volumes=[bind(f"{ENV['MEDIA_PLEX_PATH']}/library")]),
            "cloudflared": service(
                image="cloudflare/cloudflared:2026.3.0" + DIGEST,
                network_mode="host",
                healthcheck=None,
                labels={"homelab.state": "stateless", "homelab.probe": "http://:20241/ready"},
            ),
            "overseerr": service(secrets=[{"source": "TMDB_API_KEY", "target": "/run/secrets/TMDB_API_KEY"}]),
        },
        tiers={"sonarr": "app", "jellyfin": "app", "plex": "app", "cloudflared": "edge", "overseerr": "app"},
        secrets={"TMDB_API_KEY": {"name": "homelab_TMDB_API_KEY", "file": f"{ENV['HOMELAB_SECRET_DIR']}/TMDB_API_KEY"}},
    )


@pytest.mark.parametrize(
    "svc, message",
    [
        pytest.param(service(network_mode="host"), "host networking is only for the edge tier", id="host-net-on-app"),
        pytest.param(service(pid="host"), "pid: host", id="pid-host"),
        pytest.param(service(cap_add=["SYS_ADMIN"]), "SYS_ADMIN is not on the allowlist", id="cap"),
        pytest.param(
            service(devices=[{"source": "/dev/sda", "target": "/dev/sda"}]), "/dev/sda is not on the GPU", id="device"
        ),
        pytest.param(service(runtime="kata"), "runtime kata", id="runtime"),
        pytest.param(service(gpus=[{"driver": "amd", "capabilities": ["gpu"]}]), "gpus must be", id="gpu-driver"),
        pytest.param(service(gpus=[{"capabilities": ["compute"]}]), "gpus must be", id="gpu-capability"),
        pytest.param(service(gpus="1"), "gpus must be", id="gpus-count-string"),
        pytest.param(
            service(deploy={"resources": {"reservations": {"devices": [{"driver": "amd"}]}}}),
            "deploy GPU",
            id="deploy-gpu",
        ),
        pytest.param(service(volumes=[bind("/etc/passwd")]), "outside the declared site roots", id="bind-outside"),
        pytest.param(service(volumes=[bind("relative/dir")]), "outside the declared site roots", id="bind-relative"),
        pytest.param(
            service(volumes=[bind(f"{ENV['APP_DATA_DIR']}/other", "/config")]),
            "must sit under",
            id="stateful-data-dir-not-named-after-service",
        ),
        pytest.param(service(image="lscr.io/linuxserver/sonarr:latest"), "pinned by digest", id="unpinned"),
        pytest.param(service(image="evil.example/sonarr" + DIGEST), "namespace is not permitted", id="namespace"),
        pytest.param(service(healthcheck=None), "needs a healthcheck", id="no-health"),
        pytest.param(service(healthcheck={"test": ["NONE"]}), "needs a healthcheck", id="health-none"),
        pytest.param(
            service(healthcheck={"test": ["CMD", "true"], "disable": True}), "needs a healthcheck", id="health-disabled"
        ),
        pytest.param(
            service(healthcheck=None, labels={"homelab.state": "stateless", "homelab.probe": "http://:web/x"}),
            "needs a healthcheck",
            id="probe-port-not-numeric",
        ),
        pytest.param(service(labels={}), "homelab.state must be", id="no-state"),
        pytest.param(service(privileged=True), "privileged is not permitted", id="privileged"),
        pytest.param(service(volumes=[bind("/var/run/docker.sock")]), "docker.sock is not permitted", id="docker-sock"),
        pytest.param(service(build={"context": "."}), "build is not permitted", id="build"),
    ],
)
def test_each_rule_rejects_its_violation(svc, message):
    with pytest.raises(ValidationError, match=message):
        check({"svc": svc})


def test_stateless_services_may_bind_any_data_dir():
    check({"svc": service(labels={"homelab.state": "stateless"}, volumes=[bind(f"{ENV['APP_DATA_DIR']}/other")])})


def test_missing_tier_is_rejected():
    with pytest.raises(ValidationError, match="invalid tier"):
        check({"svc": service()}, tiers={})


def test_traefik_is_allowed_only_under_its_official_namespace():
    check({"traefik": service(image="docker.io/library/traefik:v3.7.17" + DIGEST)}, tiers={"traefik": "edge"})
    for image in ("traefik:v3.7.17" + DIGEST, "docker.io/library/caddy:2.11.7" + DIGEST):
        with pytest.raises(ValidationError, match="namespace is not permitted"):
            check({"traefik": service(image=image)}, tiers={"traefik": "edge"})


def test_code_plugin_images_must_be_strictly_under_its_namespace():
    namespace = {"svc": "ghcr.io/me/plugin"}
    check({"svc": service(image="ghcr.io/me/plugin/tl-auth" + DIGEST)}, namespaces=namespace)
    for image in ("ghcr.io/me/plugin" + DIGEST, "lscr.io/linuxserver/sonarr" + DIGEST):
        with pytest.raises(ValidationError, match="namespace is not permitted"):
            check({"svc": service(image=image)}, namespaces=namespace)


@pytest.mark.parametrize(
    "secret",
    [
        {"file": "/tmp/TMDB_API_KEY"},
        {"environment": "TMDB_API_KEY"},
        {"file": "/srv/homelab/data/.secrets/TMDB_API_KEY", "content": "x"},
    ],
)
def test_secrets_come_only_from_the_secret_dir(secret):
    with pytest.raises(ValidationError, match="secret must be"):
        check({"svc": service()}, secrets={"TMDB_API_KEY": secret})


@pytest.mark.parametrize("root", ["/srv/vpn", "/run", "/srv", "/srv/homelab", "relative/root"])
def test_bind_roots_may_not_overlap_protected_paths(root):
    with pytest.raises(ValidationError, match="bind root"):
        check({}, roots=(root,))


def test_data_subtree_is_exempt_but_a_symlink_to_vpn_is_not(tmp_path, monkeypatch):
    check({}, roots=("/srv/homelab/data/x",))
    real = os.path.realpath
    monkeypatch.setattr(os.path, "realpath", lambda p: "/srv/vpn" if p == "/srv/homelab/data/vpn-link" else real(p))
    with pytest.raises(ValidationError, match="overlaps protected path /srv/vpn"):
        check({}, roots=("/srv/homelab/data/vpn-link",))


def test_every_violation_is_reported_at_once():
    with pytest.raises(ValidationError) as error:
        check({"a": service(privileged=True), "b": service(cap_add=["NET_ADMIN"])})
    assert len(error.value.errors) == 2


# --- published ports -------------------------------------------------------------------------------------


def port(published, host_ip=None, protocol="tcp"):
    return {"mode": "ingress", "target": published, "published": str(published), "protocol": protocol,
            **({"host_ip": host_ip} if host_ip else {})}


@pytest.mark.parametrize("address", ["192.0.2.10", "127.0.0.1", "2001:db8::1"])
def test_a_port_on_an_explicit_address_needs_no_declaration(address):
    check({"svc": service(ports=[port(8096, address)])})


@pytest.mark.parametrize("host_ip", [None, "", "0.0.0.0", "::"])
def test_a_port_on_every_interface_must_be_declared_exposed(host_ip):
    with pytest.raises(ValidationError, match=r"svc:8096/tcp listens on every interface"):
        check({"svc": service(ports=[port(8096, host_ip)])})
    check({"svc": service(ports=[port(8096, host_ip)])}, exposed={"svc:8096/tcp"})


def test_exposure_is_per_service_port_and_protocol():
    ports = [port(51423, "0.0.0.0", "tcp"), port(51423, "0.0.0.0", "udp")]
    with pytest.raises(ValidationError, match=r"svc:51423/udp listens"):
        check({"svc": service(ports=ports)}, exposed={"svc:51423/tcp"})
    with pytest.raises(ValidationError, match=r"other:51423/tcp listens"):
        check({"other": service(ports=ports[:1])}, exposed={"svc:51423/tcp"})
    check({"svc": service(ports=ports)}, exposed={"svc:51423/tcp", "svc:51423/udp"})


def test_an_exposed_declaration_does_not_excuse_an_explicit_address_elsewhere():
    check({"svc": service(ports=[port(8096, "192.0.2.10"), port(9000, "0.0.0.0")])}, exposed={"svc:9000/tcp"})


def test_caddy_can_keep_its_image_bind_capability():
    check({"caddy": service(image="ghcr.io/jon-robbins/homelab/caddy" + DIGEST,
                             cap_drop=["ALL"], cap_add=["NET_BIND_SERVICE"])},
          tiers={"caddy": "edge"})
