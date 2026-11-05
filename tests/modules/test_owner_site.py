"""A split-disk site (movies and TV on one disk, downloads and music on another) keeps its container paths.

The fixture is shaped like a long-running real site: apps are configured against /tv, /movies, /downloads and
/music, so its overrides pin those binds. The module's shared /data mount is satisfied by an empty directory. These
tests are the contract that a catalog change must not alter what such a site's containers see or listen on.
"""

from pathlib import Path
import shutil

import pytest

from scripts.release.modules import read_env
from scripts.release.render import render

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI with Compose")
ROOT = Path(__file__).parents[2]
SITE = Path(__file__).parent / "owner_site"
ENV = read_env(SITE / "site.env")
HDD, NVME = ENV["MEDIA_HDD_PATH"], ENV["MEDIA_NVME_PATH"]
MOVIES, TV, MUSIC, DOWNLOADS = ENV["MOVIES_DIR"], ENV["TV_DIR"], ENV["MUSIC_DIR"], ENV["DOWNLOADS_DIR"]
SEEDING = f"{HDD}/.seed/Seeding"


@pytest.fixture(scope="module")
def result():
    return render(ROOT, SITE, {})


def media_view(service):
    """Container path -> (host path, read-only) for every bind that is not app config, marker or placeholder."""
    return {
        v["target"]: (v["source"], bool(v.get("read_only")))
        for v in service["volumes"]
        if v["type"] == "bind"
        and not v["target"].startswith(("/config", "/homelab-markers", "/data", "/torrent-trackers", "/app"))
    }


@pytest.mark.parametrize(
    "name, expected",
    [
        ("sonarr", {"/tv": (TV, False), "/downloads": (DOWNLOADS, False), "/seeding": (SEEDING, False)}),
        ("radarr", {"/movies": (MOVIES, False), "/downloads": (DOWNLOADS, False), "/seeding": (SEEDING, False)}),
        (
            "qbittorrent",
            {"/downloads": (DOWNLOADS, False), "/movies": (MOVIES, False), "/tv": (TV, False), "/seeding": (SEEDING, False)},
        ),
        ("bazarr", {"/tv": (TV, False), "/movies": (MOVIES, False)}),
        ("jellyfin", {"/movies": (MOVIES, True), "/tv": (TV, True), "/downloads": (DOWNLOADS, True)}),
        ("plex", {"/movies": (MOVIES, True), "/tv": (TV, True), "/mnt/media-hdd/Extras": (f"{HDD}/Extras", True)}),
        (
            "lidarr",
            {"/music": (MUSIC, False), "/downloads": (DOWNLOADS, False), "/media-nvme": (NVME, False)},
        ),
        ("soularr", {"/downloads": (DOWNLOADS, False), "/media-nvme": (NVME, False)}),
        ("slskd", {"/downloads": (DOWNLOADS, False), "/music": (MUSIC, True)}),
        ("navidrome", {"/music": (MUSIC, True)}),
    ],
)
def test_split_disk_site_keeps_every_container_path(result, name, expected):
    assert media_view(result.app["services"][name]) == expected


def test_the_shared_mount_is_only_the_empty_placeholder(result):
    """Everything that mounts the placeholder does so at /data or /data/downloads, and nothing else sees it."""
    placeholder = {
        (name, v["target"])
        for name, service in result.app["services"].items()
        for v in service.get("volumes", [])
        if v.get("type") == "bind" and v["source"].startswith(ENV["MEDIA_DIR"])
    }
    assert placeholder == {
        *((name, "/data") for name in ("sonarr", "radarr", "bazarr", "lidarr", "jellyfin", "plex")),
        *((name, "/data/downloads") for name in ("qbittorrent", "slskd", "soularr")),
    }


def test_no_extra_disk_marker_is_added_by_the_placeholder(result):
    """The placeholder sits under a root that has no marker, so no service starts to depend on a disk it did not."""
    def markers(model):
        return {
            name: sorted(v["target"] for v in s["volumes"] if v["target"].startswith("/homelab-markers"))
            for name, s in model["services"].items()
        }

    assert markers(result.app) == {
        "sonarr": ["/homelab-markers/MEDIA_HDD_PATH", "/homelab-markers/MEDIA_NVME_PATH"],
        "radarr": ["/homelab-markers/MEDIA_HDD_PATH", "/homelab-markers/MEDIA_NVME_PATH"],
        "qbittorrent": ["/homelab-markers/MEDIA_HDD_PATH", "/homelab-markers/MEDIA_NVME_PATH"],
        "bazarr": ["/homelab-markers/MEDIA_HDD_PATH"],
        "jellyfin": ["/homelab-markers/MEDIA_HDD_PATH", "/homelab-markers/MEDIA_NVME_PATH"],
        "plex": ["/homelab-markers/MEDIA_HDD_PATH"],
        "lidarr": ["/homelab-markers/MEDIA_NVME_PATH"],
        "soularr": ["/homelab-markers/MEDIA_NVME_PATH"],
        "slskd": ["/homelab-markers/MEDIA_NVME_PATH"],
        "navidrome": ["/homelab-markers/MEDIA_NVME_PATH"],
        **{n: [] for n in ("prowlarr", "flaresolverr", "overseerr", "autobrr", "authentik-postgres", "authentik-server", "authentik-worker")},
    }


def test_split_disk_site_listens_where_it_always_has(result):
    ports = {
        (name, port["published"], port["target"], port["protocol"]): port.get("host_ip")
        for model in (result.app, result.edge)
        for name, service in model["services"].items()
        for port in service.get("ports", [])
    }
    everywhere = "0.0.0.0"
    # Traefik's container targets are its entryPoints (tailnet 4443, tunnel 5443, lan 8080, lan-tls 8443);
    # the host ports stay where this site has always listened.
    assert ports == {
        ("jellyfin", "8096", 8096, "tcp"): everywhere,
        ("plex", "32400", 32400, "tcp"): everywhere,
        ("qbittorrent", "51423", 51423, "tcp"): everywhere,
        ("qbittorrent", "51423", 51423, "udp"): everywhere,
        ("slskd", "50300", 50300, "tcp"): everywhere,
        ("overseerr", "5055", 5055, "tcp"): "127.0.0.1",
        ("traefik", "443", 4443, "tcp"): ENV["TAILSCALE_IP"],
        ("traefik", "9443", 5443, "tcp"): "127.0.0.1",
        ("traefik", "80", 8080, "tcp"): None,
        ("traefik", "8443", 8443, "tcp"): None,
    }


def test_owner_keeps_gpu_access_and_optional_tmdb_secret(result):
    services = result.app["services"]
    assert services["jellyfin"]["gpus"] == services["plex"]["gpus"]
    assert services["jellyfin"]["gpus"][0]["count"] == -1
    assert services["jellyfin"]["group_add"] == ["video"]
    assert services["jellyfin"]["devices"][0]["source"] == "/dev/dri"
    assert services["overseerr"]["secrets"][0]["source"] == "TMDB_API_KEY"
    assert result.app["secrets"]["TMDB_API_KEY"]["file"] == f"{ENV['HOMELAB_SECRET_DIR']}/TMDB_API_KEY"
