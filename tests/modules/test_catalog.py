import shutil
from pathlib import Path
import re

import pytest

from scripts.release.modules import check_module, read_env
from scripts.release.render import render


ROOT = Path(__file__).parents[2]
SITE = Path(__file__).parent / "site"
ENV = read_env(SITE / "site.env")
EXPECTED = {  # tag@digest = what production runs today (verified against each registry, 2026-10-17)
    "autobrr": "ghcr.io/autobrr/autobrr:v1.85.0@sha256:f7594d15dfe9a59e7bbc4bce8aee437a1d440d60b350e2ee74331c0d4ec01928",
    "bazarr": "lscr.io/linuxserver/bazarr:v1.5.6-ls346@sha256:cb57afc3bd35558e1e7062658f9d4d18a0b4c474f10afe55f0ccfd867025f24f",
    "caddy": "ghcr.io/jon-robbins/homelab/caddy@sha256:5ec9de2201fe98cbf4bf97e32664a594e419b89aaa7169a5e9aa2efe165ab82f",
    "cloudflared": "cloudflare/cloudflared:2026.3.0@sha256:6b599ca3e974349ead3286d178da61d291961182ec3fe9c505e1dd02c8ac31b0",
    "flaresolverr": "ghcr.io/flaresolverr/flaresolverr:v3.4.6@sha256:7962759d99d7e125e108e0f5e7f3cdbcd36161776d058d1d9b7153b92ef1af9e",
    "homelab-arr-missing-search": "ghcr.io/jon-robbins/homelab/homelab-workers@sha256:6b5fa615d3fdfcf2f49404ac5683b8d683cabdb442787fc63c2d800bfeefa823",
    "homelab-arr-post-import": "ghcr.io/jon-robbins/homelab/homelab-workers@sha256:6b5fa615d3fdfcf2f49404ac5683b8d683cabdb442787fc63c2d800bfeefa823",
    "homelab-qbt-events": "ghcr.io/jon-robbins/homelab/homelab-workers@sha256:6b5fa615d3fdfcf2f49404ac5683b8d683cabdb442787fc63c2d800bfeefa823",
    "homelab-seerr-request-reconciler": "ghcr.io/jon-robbins/homelab/homelab-workers@sha256:6b5fa615d3fdfcf2f49404ac5683b8d683cabdb442787fc63c2d800bfeefa823",
    "jellyfin": "lscr.io/linuxserver/jellyfin:10.11.8ubu2404-ls29@sha256:ae1ac3d89b8598f31f5e1dc5aff898e13eab617303db58f84956e26f7b78c198",
    "overseerr": "ghcr.io/seerr-team/seerr:v3.2.0@sha256:c4cbd5121236ac2f70a843a0b920b68a27976be57917555f1c45b08a1e6b2aad",
    "plex": "lscr.io/linuxserver/plex:1.43.4.10903-e5521bd8c-ls328@sha256:fc852e4c21359252896227aaafd48d7f052c0142cbe8e1ac3068d060faeb6d56",
    "prowlarr": "lscr.io/linuxserver/prowlarr:2.3.5.5327-ls143@sha256:c5de2a8758a05594319263e7691c1dce56899442ed1720d6eca216c0958f4caf",
    "qbittorrent": "lscr.io/linuxserver/qbittorrent:5.1.4-r3-ls451@sha256:8f7a1da9644340c737e3211ecc910a416d4295076e6b2824a4afcd1f4e3576e2",
    "radarr": "lscr.io/linuxserver/radarr:6.1.1.10360-ls300@sha256:b01097ad2d948c9f5eca39eb60bb529e2e55b0738c4bf7db09383bef0abab59d",
    "sonarr": "lscr.io/linuxserver/sonarr:4.0.17.2952-ls309@sha256:3580aec3802c915f0f819a88d5099abce61734b925732b8393d176b5dc561020",
}


def _modules():
    return sorted(ROOT.glob("modules/*/*.yml"))


@pytest.mark.parametrize("path", _modules(), ids=lambda path: path.stem)
def test_catalog_modules_validate_and_match_inventory(path):
    check_module(path, ENV)
    assert re.search(r"(?m)^\s*image:\s*(\S+)$", path.read_text()).group(1) == EXPECTED[path.stem]


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI with Compose")
def test_catalog_site_renders_every_module():
    result = render(ROOT, SITE, {})
    assert set(result.app["services"]) == {path.stem for path in ROOT.glob("modules/app/*.yml")}
    assert set(result.edge["services"]) == {"caddy", "cloudflared"}


def test_catalog_render_has_production_limits_and_service_settings():
    services = render(ROOT, SITE, {}).app["services"]
    assert {name: (service["mem_limit"], service["memswap_limit"]) for name, service in services.items()
            if "mem_limit" in service} == {
        "autobrr": ("536870912", "1073741824"),
        "flaresolverr": ("2147483648", "4294967296"),
        "plex": ("8589934592", "17179869184"),
        "qbittorrent": ("4294967296", "8589934592"),
    }
    expected_pids = {
        "homelab-arr-missing-search": 100,
        "homelab-qbt-events": 100,
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
    qbt_events = services["homelab-qbt-events"]
    assert qbt_events["user"] == "1000:1000"
    assert qbt_events["labels"]["homelab.state"] == "stateful"
    [state] = qbt_events["volumes"]
    assert (state["type"], state["source"], state["target"]) == ("bind", "/tmp/homelab-catalog/data/qbt-events", "/state")
    assert not state.get("bind", {}).get("create_host_path")  # compose prints false as an empty bind block
    assert services["jellyfin"]["group_add"] == ["video"]
