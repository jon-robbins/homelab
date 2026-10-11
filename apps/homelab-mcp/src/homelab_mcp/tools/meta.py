from __future__ import annotations

import time

from homelab_mcp.config import Settings
from homelab_mcp.capabilities import capability_for
from homelab_mcp.envelope import ok_response

TOOL_CAPABILITIES = "homelab_mcp_capabilities"

V1_ENABLED_TOOLS = [
    {
        "name": "homelab_mcp_capabilities",
        "phase": "v1",
        "enabled": True,
        "description": "List available MCP tools and their capabilities",
    },
    {
        "name": "qbt_transfer_summary",
        "phase": "v1",
        "enabled": True,
        "description": "Global qBittorrent transfer and connection status",
    },
    {
        "name": "qbt_preferences_snapshot",
        "phase": "v1",
        "enabled": True,
        "description": "qBittorrent preference snapshot and profile hint",
    },
    {
        "name": "qbt_torrents_summary",
        "phase": "v1",
        "enabled": True,
        "description": "Aggregated torrent counts by state and activity",
    },
    {
        "name": "qbt_torrents_list",
        "phase": "v1",
        "enabled": True,
        "description": "Paginated qBittorrent torrent list with optional filtering",
    },
    {
        "name": "qbt_torrent_detail",
        "phase": "v1",
        "enabled": True,
        "description": "Detailed view of a torrent including trackers and peers",
    },
    {
        "name": "homelab_services_health",
        "phase": "v1",
        "enabled": True,
        "description": "HTTP health probes for v1 homelab services",
    },
]

V1_1_ENABLED_TOOLS = [
    {
        "name": "sonarr_queue_snapshot",
        "phase": "v1.1",
        "enabled": True,
        "description": "Active Sonarr queue snapshot",
    },
    {
        "name": "radarr_queue_snapshot",
        "phase": "v1.1",
        "enabled": True,
        "description": "Active Radarr queue snapshot",
    },
    {
        "name": "prowlarr_indexer_health",
        "phase": "v1.1",
        "enabled": True,
        "description": "Prowlarr indexer health and failures",
    },
    {
        "name": "overseerr_request_summary",
        "phase": "v1.1",
        "enabled": True,
        "description": "Overseerr pending and processing requests",
    },
    {
        "name": "overseerr_request_pipeline",
        "phase": "v1.1",
        "enabled": True,
        "description": "Trace recent Overseerr requests through Arr handoff into qBittorrent",
    },
    {
        "name": "bazarr_missing_subtitles_summary",
        "phase": "v1.1",
        "enabled": True,
        "description": "Missing subtitles summary by language",
    },
    {
        "name": "media_paths_usage",
        "phase": "v1.1",
        "enabled": True,
        "description": "NVMe and HDD path usage summary",
    },
    {
        "name": "qbt_peer_port_verify",
        "phase": "v1.1",
        "enabled": True,
        "description": "Verify qBittorrent peer port config and connection status",
    },
]

V2_0_READONLY_TOOLS = [
    {
        "name": "qbt_offload_candidates",
        "phase": "v2.0",
        "enabled": True,
        "description": "List dry-run NVMe to HDD offload candidates",
    },
]

V2_0_MUTATION_TOOLS = [
    {
        "name": "qbt_torrents_resume",
        "phase": "v2.0",
        "enabled": False,
        "description": "Resume torrents by hash list or tag",
    },
]

V2_1_MUTATION_TOOLS = [
    {
        "name": "qbt_apply_profile",
        "phase": "v2.1",
        "enabled": False,
        "description": "Apply a qBittorrent preference profile",
    },
    {
        "name": "qbt_torrents_pause",
        "phase": "v2.1",
        "enabled": False,
        "description": "Pause torrents by hash list or tag",
    },
    {
        "name": "qbt_offload_run",
        "phase": "v2.1",
        "enabled": False,
        "description": "Execute NVMe to HDD offload with explicit confirmation",
    },
]

V2_4_READONLY_TOOLS = [
    {
        "name": "arr_indexer_sync_health",
        "phase": "v2.4",
        "enabled": True,
        "description": "Radarr and Sonarr enabled indexer counts (Prowlarr sync preflight)",
    },
    {
        "name": "radarr_release_search",
        "phase": "v2.4",
        "enabled": True,
        "description": "Ranked Radarr releases for a library movie",
    },
    {
        "name": "sonarr_release_search",
        "phase": "v2.4",
        "enabled": True,
        "description": "Ranked Sonarr releases for a library series season",
    },
    {
        "name": "seerr_request_diagnosis",
        "phase": "v2.4",
        "enabled": True,
        "description": "Cached Seerr request diagnosis from reconciler state",
    },
    {
        "name": "prowlarr_indexer_search",
        "phase": "v2.4",
        "enabled": True,
        "description": "Direct Prowlarr indexer search for non-library grabs",
    },
]

V2_4_MUTATION_TOOLS = [
    {
        "name": "radarr_release_grab",
        "phase": "v2.4",
        "enabled": False,
        "description": "Grab a Radarr release with tier-1 expected_action gate",
    },
    {
        "name": "sonarr_release_grab",
        "phase": "v2.4",
        "enabled": False,
        "description": "Grab a Sonarr release with tier-1 expected_action gate",
    },
    {
        "name": "prowlarr_indexer_grab",
        "phase": "v2.4",
        "enabled": False,
        "description": "Grab a Prowlarr indexer release with tier-1 expected_action gate",
    },
]

V2_5_READONLY_TOOLS = [
    {
        "name": "qbt_exclusive_status",
        "phase": "v2.5",
        "enabled": True,
        "description": "Read exclusive qBittorrent download session status from homelab-qbt-events",
    },
]

V2_5_MUTATION_TOOLS = [
    {
        "name": "sonarr_season_ensure_search",
        "phase": "v2.5",
        "enabled": False,
        "description": "Monitor a Sonarr season and queue SeasonSearch (tier-1)",
    },
    {
        "name": "qbt_torrent_force_start",
        "phase": "v2.5",
        "enabled": False,
        "description": "Top-prio + force-start a qBittorrent torrent (tier-2)",
    },
    {
        "name": "qbt_exclusive_prioritize",
        "phase": "v2.5",
        "enabled": False,
        "description": "Start exclusive name-match download session (tier-2)",
    },
    {
        "name": "qbt_exclusive_cancel",
        "phase": "v2.5",
        "enabled": False,
        "description": "Cancel active exclusive download session (tier-2)",
    },
]

V2_6_READONLY_TOOLS = [
    {
        "name": "arr_lookup",
        "phase": "v2.6",
        "enabled": True,
        "description": "Resolve movie/series candidates with library membership (read)",
    },
    {
        "name": "arr_status",
        "phase": "v2.6",
        "enabled": True,
        "description": "Library item status: files, queue, history (read)",
    },
]

V2_6_MUTATION_TOOLS = [
    {
        "name": "arr_ensure",
        "phase": "v2.6",
        "enabled": False,
        "description": "Add/monitor movie or series season without auto-search (tier-1)",
    },
    {
        "name": "arr_queue_remove",
        "phase": "v2.6",
        "enabled": False,
        "description": "Remove an Arr queue item (tier-2)",
    },
]

V2_8_READONLY_TOOLS = [
    {
        "name": "media_title_to_nvme_plan",
        "phase": "v2.8",
        "enabled": True,
        "description": "Read-only plan for staging a movie, season, series, or episode onto NVMe",
    },
    {
        "name": "media_title_to_nvme_status",
        "phase": "v2.8",
        "enabled": True,
        "description": "Read-only current staging status for a media title scope",
    },
]

PLEX_READONLY_TOOLS = [
    {
        "name": "plex_library_search",
        "phase": "v2.8",
        "enabled": True,
        "description": "Search the local Plex library for a title, year, and kind",
    },
    {
        "name": "media_visibility_diagnosis",
        "phase": "v2.8",
        "enabled": True,
        "description": "Diagnose whether a title exists in Plex and Arr libraries",
    },
    {
        "name": "plex_watch_status",
        "phase": "v2.8",
        "enabled": True,
        "description": "Report a Plex user's current playback and show progress",
    },
]

def homelab_mcp_capabilities(
    settings: Settings,
    *,
    registered_tools: set[str],
    extension_tools: list[dict[str, object]] | None = None,
    extension_roles: dict[str, tuple[str, str]] | None = None,
) -> str:
    started = time.perf_counter()
    descriptors = [
        *V1_ENABLED_TOOLS,
        *V1_1_ENABLED_TOOLS,
        *V2_0_READONLY_TOOLS,
        *V2_4_READONLY_TOOLS,
        *V2_5_READONLY_TOOLS,
        *V2_6_READONLY_TOOLS,
        *V2_8_READONLY_TOOLS,
        *PLEX_READONLY_TOOLS,
        *V2_0_MUTATION_TOOLS,
        *V2_1_MUTATION_TOOLS,
        *V2_4_MUTATION_TOOLS,
        *V2_5_MUTATION_TOOLS,
        *V2_6_MUTATION_TOOLS,
        *(extension_tools or ()),
    ]
    tools = []
    described_names = set()
    for descriptor in descriptors:
        name = descriptor["name"]
        try:
            capability = capability_for(name, extension_roles)
        except KeyError:
            if name in registered_tools:
                raise RuntimeError(f"registered MCP tool is unclassified: {name}")
            continue
        entry = {key: value for key, value in descriptor.items() if key != "mutation"}
        entry["enabled"] = name in registered_tools
        entry["capability"] = capability
        tools.append(entry)
        described_names.add(name)
    missing = registered_tools - described_names
    if missing:
        raise RuntimeError("registered MCP tools lack capability descriptors: " + ", ".join(sorted(missing)))
    data = {
        "version": "0.10.0",
        "mutations_enabled": settings.enable_mutations,
        "tools": tools,
    }
    duration_ms = int((time.perf_counter() - started) * 1000)
    return ok_response(TOOL_CAPABILITIES, data, duration_ms=duration_ms)
