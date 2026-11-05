import shutil
from pathlib import Path
import re

import pytest

from scripts.release.modules import check_module, read_env
from scripts.release.render import render


ROOT = Path(__file__).parents[2]
SITE = Path(__file__).parent / "site"
ENV = read_env(SITE / "site.env")
_COMPOSE_FILENAME = re.compile(r"(docker-)?compose(-[\w]+)?(?>\.[\w-]+)?\.ya?ml", re.IGNORECASE)


def _modules():
    return sorted(ROOT.glob("modules/*/*.compose.yml"))


def _service(path):
    return path.name.removesuffix(".compose.yml")


def test_catalog_files_have_compose_recognized_names():
    invalid = [
        path.relative_to(ROOT)
        for path in ROOT.glob("modules/*/*")
        if path.is_file() and not _COMPOSE_FILENAME.search(path.name)
    ]
    assert invalid == []


@pytest.mark.parametrize("path", _modules(), ids=lambda path: path.stem)
def test_catalog_modules_validate(path):
    check_module(path, ENV)


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI with Compose")
def test_catalog_site_renders_every_module():
    result = render(ROOT, SITE, {})
    assert set(result.app["services"]) == {_service(path) for path in ROOT.glob("modules/app/*.compose.yml")}
    assert set(result.edge["services"]) == {"traefik", "cloudflared"}


def test_catalog_render_has_production_limits_and_service_settings():
    services = render(ROOT, SITE, {}).app["services"]
    edge = render(ROOT, SITE, {}).edge["services"]
    assert edge["traefik"]["user"] == "1000:1000"
    assert edge["traefik"]["cap_drop"] == ["ALL"]
    assert "cap_add" not in edge["traefik"]
    assert services["overseerr"]["user"] == "1000:1000"
    for name in ("sonarr", "radarr", "lidarr", "bazarr", "qbittorrent"):
        assert services[name]["environment"]["UMASK"] == "002"
    assert services["plex"]["cap_drop"] == ["ALL"]
    assert services["plex"]["cap_add"] == ["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE", "FOWNER"]
    assert {name: (service["mem_limit"], service["memswap_limit"]) for name, service in services.items()
            if "mem_limit" in service} == {
        "authentik-postgres": ("536870912", "1073741824"),
        "authentik-server": ("1073741824", "2147483648"),
        "authentik-worker": ("1073741824", "2147483648"),
        "autobrr": ("536870912", "1073741824"),
        "flaresolverr": ("2147483648", "4294967296"),
        "plex": ("8589934592", "17179869184"),
        "qbittorrent": ("4294967296", "8589934592"),
    }
    expected_pids = {
        "authentik-postgres": 200,
        "authentik-server": 500,
        "authentik-worker": 500,
        "autobrr": 200,
        "qbittorrent": 200,
        "bazarr": 500,
        "flaresolverr": 500,
        "jellyfin": 500,
        "overseerr": 500,
        "plex": 500,
        "prowlarr": 500,
        "radarr": 500,
        "sonarr": 500,
    }
    assert {name: services[name]["pids_limit"] for name in expected_pids} == expected_pids


def test_catalog_authentik_modules():
    services = render(ROOT, SITE, {}).app["services"]
    postgres, server, worker = (services[f"authentik-{part}"] for part in ("postgres", "server", "worker"))
    assert not any("docker.sock" in str(volume) for volume in worker.get("volumes", []))
    assert worker["user"] == "1000:1000"
    both = {"AUTHENTIK_SECRET_KEY", "AUTHENTIK_PG_PASSWORD"}
    assert {secret["target"].removeprefix("/run/secrets/") for secret in server["secrets"]} == both
    assert {secret["target"].removeprefix("/run/secrets/") for secret in worker["secrets"]} == both
    assert {secret["target"].removeprefix("/run/secrets/") for secret in postgres["secrets"]} == {"AUTHENTIK_PG_PASSWORD"}
    for service in (postgres, server, worker):
        for key, value in service["environment"].items():
            if "PASSWORD" in key or "SECRET" in key:
                assert value.startswith("file:///run/secrets/") or key.endswith("_FILE")
        assert "ports" not in service
    assert server["depends_on"]["authentik-postgres"]["condition"] == "service_healthy"
    assert worker["depends_on"]["authentik-postgres"]["condition"] == "service_healthy"


# --- one disk: the shared media mount, explicit ports, no GPU ------------------------------------------------


def _mounts(service):
    return {v["target"]: v for v in service.get("volumes", []) if v["type"] == "bind"}


def test_importing_apps_see_downloads_and_library_through_one_mount():
    """A hardlink can't cross mounts, so an import only hardlinks when both paths sit under one /data mount."""
    services = render(ROOT, SITE, {}).app["services"]
    media = ENV["MEDIA_DIR"]
    for name in ("sonarr", "radarr", "lidarr", "bazarr"):
        mounts = _mounts(services[name])
        assert mounts["/data"]["source"] == media and not mounts["/data"].get("read_only"), name
        assert not {"/tv", "/movies", "/music", "/downloads"} & set(mounts), name
    assert _mounts(services["qbittorrent"])["/data/downloads"]["source"] == f"{media}/downloads"
    assert {"/tv", "/movies", "/downloads"}.isdisjoint(_mounts(services["qbittorrent"]))


def test_library_readers_mount_read_only_and_downloaders_only_see_downloads():
    services = render(ROOT, SITE, {}).app["services"]
    for name in ("jellyfin", "plex"):
        assert _mounts(services[name])["/data"]["read_only"] is True
    assert _mounts(services["navidrome"])["/music"]["read_only"] is True
    for name in ("qbittorrent", "slskd", "soularr"):
        assert _mounts(services[name])["/data/downloads"]["source"] == f"{ENV['MEDIA_DIR']}/downloads"
        assert "/data" not in _mounts(services[name]) or name == "soularr"  # soularr's /data is its own state


def test_media_mounts_carry_the_disk_marker_once():
    services = render(ROOT, SITE, {}).app["services"]
    for name in ("sonarr", "qbittorrent", "jellyfin"):
        markers = [v for v in services[name]["volumes"] if v["target"] == "/homelab-markers/MEDIA_DIR"]
        assert len(markers) == 1 and markers[0]["read_only"] is True


def test_published_ports_bind_the_site_addresses():
    result = render(ROOT, SITE, {})
    published = {
        (name, port["published"], port["protocol"]): port["host_ip"]
        for model in (result.app, result.edge)
        for name, service in model["services"].items()
        for port in service.get("ports", [])
    }
    media, peer, tailnet = ENV["MEDIA_BIND_IP"], ENV["PEER_BIND_IP"], ENV["TAILSCALE_IP"]
    assert published == {
        ("jellyfin", "8096", "tcp"): media,
        ("plex", "32400", "tcp"): media,
        ("qbittorrent", "51423", "tcp"): peer,
        ("qbittorrent", "51423", "udp"): peer,
        ("slskd", "50300", "tcp"): peer,
        ("overseerr", "5055", "tcp"): "127.0.0.1",
        ("traefik", "443", "tcp"): tailnet,
    }


def test_seerr_needs_no_tmdb_key():
    app = render(ROOT, SITE, {}).app
    assert "secrets" not in app["services"]["overseerr"]
    assert "TMDB_API_KEY" not in app.get("secrets", {})


def test_stranger_catalog_needs_no_gpu_runtime_or_devices():
    for service in render(ROOT, SITE, {}).app["services"].values():
        assert not service.get("gpus")
        assert not service.get("devices")
        assert not service.get("deploy", {}).get("resources", {}).get("reservations", {}).get("devices")
