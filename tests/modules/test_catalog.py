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
    assert set(result.edge["services"]) == {"caddy", "cloudflared"}


def test_catalog_render_has_production_limits_and_service_settings():
    services = render(ROOT, SITE, {}).app["services"]
    edge = render(ROOT, SITE, {}).edge["services"]
    assert edge["caddy"]["user"] == "1000:1000"
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
    assert services["jellyfin"]["group_add"] == ["video"]


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
