from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ServiceEntry:
    name: str
    compose_file: str
    base_url: str
    health_path: str
    phase: str = "v1"
    auth_header: str | None = None


V1_SERVICES: tuple[ServiceEntry, ...] = (
    ServiceEntry("qbittorrent", "media.yml", "http://qbittorrent:8080", "/"),
    ServiceEntry(
        "sonarr",
        "media.yml",
        "http://sonarr:8989/sonarr",
        "/api/v3/system/status",
        auth_header="X-Api-Key",
    ),
    ServiceEntry(
        "radarr",
        "media.yml",
        "http://radarr:7878/radarr",
        "/api/v3/system/status",
        auth_header="X-Api-Key",
    ),
    ServiceEntry(
        "prowlarr",
        "media.yml",
        "http://prowlarr:9696/prowlarr",
        "/api/v1/system/status",
        auth_header="X-Api-Key",
    ),
    ServiceEntry("overseerr", "media.yml", "http://overseerr:5055", "/api/v1/status"),
    ServiceEntry(
        "autobrr",
        "autobrr.yml",
        "http://autobrr:7474",
        "/api/healthz/liveness",
        phase="v1.2",
        auth_header="X-API-Token",
    ),
    ServiceEntry("caddy", "network.yml", "http://caddy:2019", "/config/"),
    ServiceEntry("jellyfin", "media.yml", "http://jellyfin:8096", "/health"),
    ServiceEntry("plex", "media.yml", "http://plex:32400", "/identity"),
    ServiceEntry(
        "media-agent",
        "media.yml",
        "http://media-agent:8000",
        "/internal/media-agent/v1/health",
        auth_header="Authorization",
    ),
)


def catalog_payload() -> dict[str, Any]:
    return {
        "services": [
            {
                "name": service.name,
                "compose_file": service.compose_file,
                "internal_base_url": service.base_url,
                "health_path": service.health_path,
                "phase": service.phase,
            }
            for service in V1_SERVICES
        ]
    }
