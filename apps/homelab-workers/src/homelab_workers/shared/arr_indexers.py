from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homelab_workers.shared.arr import ArrClient

SYNC_HINT = (
    "Radarr or Sonarr reports zero enabled indexers. Check Prowlarr indexer sync and "
    "Caddy /prowlarr routing (docs/ingress.md)."
)


@dataclass(frozen=True)
class ArrIndexerAppHealth:
    enabled_indexers: int
    healthy: bool


def indexer_health_for_client(client: ArrClient) -> ArrIndexerAppHealth:
    count = client.indexers_enabled_count()
    return ArrIndexerAppHealth(enabled_indexers=count, healthy=count > 0)


def arr_indexer_sync_health(
    *,
    radarr: ArrClient | None,
    sonarr: ArrClient | None,
) -> dict[str, Any]:
    radarr_health = (
        indexer_health_for_client(radarr)
        if radarr is not None
        else ArrIndexerAppHealth(enabled_indexers=0, healthy=False)
    )
    sonarr_health = (
        indexer_health_for_client(sonarr)
        if sonarr is not None
        else ArrIndexerAppHealth(enabled_indexers=0, healthy=False)
    )
    healthy = radarr_health.healthy and sonarr_health.healthy
    return {
        "radarr": {
            "enabled_indexers": radarr_health.enabled_indexers,
            "healthy": radarr_health.healthy,
        },
        "sonarr": {
            "enabled_indexers": sonarr_health.enabled_indexers,
            "healthy": sonarr_health.healthy,
        },
        "healthy": healthy,
        "hint": None if healthy else SYNC_HINT,
    }
