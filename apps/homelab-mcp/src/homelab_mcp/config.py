from __future__ import annotations

import os
from dataclasses import dataclass

from homelab_mcp.secrets import MCP_MANAGED_SECRETS, hydrate_managed_secrets


@dataclass(frozen=True)
class Settings:
    mcp_api_token: str
    host: str
    port: int
    qbt_url: str
    qbt_events_url: str
    qbt_username: str
    qbt_password: str
    sonarr_url: str
    sonarr_api_key: str
    radarr_url: str
    radarr_api_key: str
    prowlarr_url: str
    prowlarr_api_key: str
    overseerr_url: str
    overseerr_api_key: str
    bazarr_url: str
    bazarr_api_key: str
    media_nvme_mount: str
    media_hdd_mount: str
    qbt_offload_min_hdd_free_bytes: int
    qbt_peer_port: int
    media_agent_token: str
    autobrr_url: str
    autobrr_api_key: str
    enable_mutations: bool
    audit_path: str
    qbt_media_stage_excluded_labels: frozenset[str]
    release_search_timeout_s: float
    release_search_poll_s: float
    release_search_limit: int
    seerr_reconciler_state_path: str
    seerr_reconciler_status_path: str
    seerr_reconciler_status_max_age_seconds: int
    plex_internal_url: str
    plex_token: str | None


def load_settings() -> Settings:
    hydrate_managed_secrets(MCP_MANAGED_SECRETS)
    return Settings(
        mcp_api_token=os.environ.get("MCP_API_TOKEN", ""),
        host=os.environ.get("HOMELAB_MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("HOMELAB_MCP_PORT", "8090")),
        qbt_url=os.environ.get("QBITTORRENT_INTERNAL_URL", "http://qbittorrent:8080"),
        qbt_events_url=os.environ.get(
            "QBT_EVENTS_INTERNAL_URL", "http://homelab-qbt-events:8101"
        ),
        qbt_username=os.environ.get("QBITTORRENT_USERNAME", ""),
        qbt_password=os.environ.get("QBITTORRENT_PASSWORD", ""),
        sonarr_url=os.environ.get("SONARR_INTERNAL_URL", "http://sonarr:8989/sonarr"),
        sonarr_api_key=os.environ.get("SONARR_API_KEY", ""),
        radarr_url=os.environ.get("RADARR_INTERNAL_URL", "http://radarr:7878/radarr"),
        radarr_api_key=os.environ.get("RADARR_API_KEY", ""),
        prowlarr_url=os.environ.get(
            "PROWLARR_INTERNAL_URL", "http://prowlarr:9696/prowlarr"
        ),
        prowlarr_api_key=os.environ.get("PROWLARR_API_KEY", ""),
        overseerr_url=os.environ.get("OVERSEERR_INTERNAL_URL", "http://overseerr:5055"),
        overseerr_api_key=os.environ.get("OVERSEERR_API_KEY", ""),
        bazarr_url=os.environ.get("BAZARR_INTERNAL_URL", "http://bazarr:6767/bazarr"),
        bazarr_api_key=os.environ.get("BAZARR_API_KEY", ""),
        media_nvme_mount=os.environ.get("MEDIA_NVME_MOUNT", "/mnt/media-nvme"),
        media_hdd_mount=os.environ.get("MEDIA_HDD_MOUNT", "/mnt/media-hdd"),
        qbt_offload_min_hdd_free_bytes=int(
            os.environ.get("QBT_OFFLOAD_MIN_HDD_FREE_BYTES", "10737418240")
        ),
        qbt_peer_port=int(os.environ.get("QBITTORRENT_PEER_PORT", "51423")),
        media_agent_token=os.environ.get("MEDIA_AGENT_TOKEN", ""),
        autobrr_url=os.environ.get("AUTOBRR_INTERNAL_URL", "http://autobrr:7474"),
        autobrr_api_key=os.environ.get("AUTOBRR_API_KEY", ""),
        enable_mutations=os.environ.get("HOMELAB_MCP_ENABLE_MUTATIONS", "0") == "1",
        audit_path=os.environ.get("HOMELAB_MCP_AUDIT_PATH", "/data/audit.jsonl"),
        qbt_media_stage_excluded_labels=frozenset(
            part.strip().lower()
            for part in os.environ.get("QBT_MEDIA_STAGE_EXCLUDED_LABELS", "").split(",")
            if part.strip()
        ),
        release_search_timeout_s=float(
            os.environ.get("MCP_RELEASE_SEARCH_TIMEOUT_S", "30")
        ),
        release_search_poll_s=float(os.environ.get("MCP_RELEASE_SEARCH_POLL_S", "2")),
        release_search_limit=int(os.environ.get("MCP_RELEASE_SEARCH_LIMIT", "10")),
        seerr_reconciler_state_path=os.environ.get(
            "SEERR_RECONCILER_STATE_PATH", "/seerr-request-reconciler/state.json"
        ),
        seerr_reconciler_status_path=os.environ.get(
            "SEERR_RECONCILER_STATUS_PATH", "/seerr-request-reconciler/status.json"
        ),
        seerr_reconciler_status_max_age_seconds=int(
            os.environ.get("SEERR_RECONCILER_STATUS_MAX_AGE_SECONDS", "900")
        ),
        plex_internal_url=os.environ.get("PLEX_INTERNAL_URL", "http://plex:32400"),
        plex_token=os.environ.get("PLEX_TOKEN") or None,
    )
