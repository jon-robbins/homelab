from __future__ import annotations

import pytest

from homelab_mcp.config import Settings


@pytest.fixture
def mcp_settings() -> Settings:
    return Settings(
        mcp_api_token="token",
        host="0.0.0.0",
        port=8090,
        qbt_url="http://qbittorrent:8080",
        qbt_events_url="http://homelab-qbt-events:8101",
        qbt_username="user",
        qbt_password="pass",
        sonarr_url="http://sonarr:8989/sonarr",
        sonarr_api_key="sonarr-key",
        radarr_url="http://radarr:7878/radarr",
        radarr_api_key="radarr-key",
        prowlarr_url="http://prowlarr:9696/prowlarr",
        prowlarr_api_key="prowlarr-key",
        overseerr_url="http://overseerr:5055",
        overseerr_api_key="overseerr-key",
        bazarr_url="http://bazarr:6767/bazarr",
        bazarr_api_key="bazarr-key",
        media_nvme_mount="/mnt/media-nvme",
        media_hdd_mount="/mnt/media-hdd",
        qbt_offload_min_hdd_free_bytes=10_737_418_240,
        qbt_peer_port=51423,
        media_agent_token="media-token",
        autobrr_url="http://autobrr:7474",
        autobrr_api_key="autobrr-token",
        enable_mutations=False,
        audit_path="/tmp/test-audit.jsonl",
        qbt_media_stage_excluded_labels=frozenset(),
        release_search_timeout_s=30.0,
        release_search_poll_s=2.0,
        release_search_limit=10,
        seerr_reconciler_state_path="/tmp/seerr-state.json",
        seerr_reconciler_status_path="/tmp/seerr-status.json",
        seerr_reconciler_status_max_age_seconds=900,
        plex_internal_url="http://plex:32400",
        plex_token=None,
    )
