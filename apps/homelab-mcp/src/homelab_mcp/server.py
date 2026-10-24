from __future__ import annotations

import functools
from typing import Literal

from mcp.server.fastmcp import FastMCP

from homelab_mcp.config import load_settings
from homelab_mcp.extensions import ExtensionContext, load_configured_extensions
from homelab_mcp.mcp_schema import (
    AddExclusion,
    ArrDeleteMediaType,
    ArrLibraryId,
    AutobrrRecentLimit,
    DeleteFiles,
    FilesListLimit,
    FilterEnabled,
    FilterName,
    IrcNetworkName,
    MediaPath,
    MediaPathOptional,
    SeerrAction,
    SeerrRequestIdRequired,
    TrashOlderThanDays,
    ArrQuery,
    ArrSeason,
    EpisodeNumber,
    BazarrScanLimit,
    CategoryFilter,
    ConfirmFlag,
    ExpectedAction,
    GrabEpisodeId,
    GrabGuid,
    GrabIndexerId,
    GrabMovieId,
    GrabSizeBytes,
    GrabTitle,
    HashPrefix,
    IncludeSeriesPacks,
    IndexerIdsFilter,
    LibraryId,
    MediaSearchQuery,
    MediaSearchQueryOptional,
    MediaType,
    MediaStageScope,
    MovieIdFilter,
    NameContains,
    OffloadLimit,
    OverseerrLookbackHours,
    OverseerrRequestLimit,
    PauseTag,
    QbtProfileName,
    QueueId,
    ReleaseSearchLimit,
    ResumeTag,
    SeerrDiagnosisLookbackHours,
    SeerrDiagnosisTitle,
    SeerrRequestId,
    SeasonNumber,
    SeriesIdFilter,
    ServicesFilter,
    ProwlarrSearchType,
    TagFilter,
    TmdbId,
    TorrentHashSingle,
    TorrentHashes,
    TorrentListLimit,
    TorrentNameContains,
    TorrentListOffset,
    TorrentStateFilter,
    TvdbId,
    make_register_tool,
)
from homelab_mcp.tools import autobrr, health, media, media_files, meta, qbt
from homelab_mcp.tools.mutations import autobrr as mut_autobrr
from homelab_mcp.tools.mutations import library as mut_library
from homelab_mcp.tools.mutations import media_downloads as mut_media_downloads
from homelab_mcp.tools.mutations import media_files as mut_files
from homelab_mcp.tools.mutations import qbt as mut_qbt
from homelab_mcp.tools.plex import (
    plex_library_search as _plex_library_search,
    media_visibility_diagnosis as _media_visibility_diagnosis,
    plex_watch_status as _plex_watch_status,
)

settings = load_settings()
extension_capability_descriptors: list[dict[str, object]] = []
release_grab_guards = []
extension_tool_roles: dict[str, tuple[str, str]] = {}
extension_qbt_profiles: dict[str, dict[str, object]] = {}
autobrr_pipeline_checks: list = []
mcp = FastMCP("homelab", host=settings.host, port=settings.port)
_configured_profile = __import__("os").environ.get("HOMELAB_MCP_TOOL_PROFILE")
tool = make_register_tool(
    mcp,
    profile=_configured_profile,
    enable_mutations=settings.enable_mutations,
    extension_roles=extension_tool_roles,
)

plex_search_bound = functools.partial(_plex_library_search, _settings=settings)
plex_visibility_bound = functools.partial(_media_visibility_diagnosis, _settings=settings)
plex_watch_status_bound = functools.partial(_plex_watch_status, _settings=settings)


@tool(
    "List homelab-mcp version, enabled tools, and roadmap entries.",
    "- version (str): package version\n"
    "- tools (array): name, phase, enabled, description for each tool",
)
def homelab_mcp_capabilities() -> str:
    return meta.homelab_mcp_capabilities(
        settings,
        registered_tools={tool.name for tool in mcp._tool_manager.list_tools()},
        extension_tools=extension_capability_descriptors,
        extension_roles=extension_tool_roles,
    )


@tool(
    "HTTP health probes for homelab compose services.",
    "- results (array): per-service reachable, http_status, latency_ms, status, detail\n"
    "- summary (object): healthy_count, degraded_count, down_count, skipped_count",
)
def homelab_services_health(services: ServicesFilter = None) -> str:
    return health.homelab_services_health(settings, services=services)


@tool(
    "Global qBittorrent transfer and connection status.",
    "- connection_status (str): connected, firewalled, or disconnected\n"
    "- last_external_address_v4 (str|null)\n"
    "- dl_speed_bps, up_speed_bps, dl_rate_limit_bps, up_rate_limit_bps (int)\n"
    "- dht_nodes (int)",
)
def qbt_transfer_summary() -> str:
    return qbt.transfer_summary(settings)


@tool(
    "qBittorrent preference snapshot and inferred profile hint.",
    "- queueing_enabled, max_active_downloads, max_active_uploads (mixed)\n"
    "- max_connec, max_connec_per_torrent, dl_limit, up_limit\n"
    "- profile_hint (str): inferred active profile label",
)
def qbt_preferences_snapshot() -> str:
    return qbt.preferences_snapshot(settings, extension_profiles=extension_qbt_profiles)


@tool(
    "Aggregated qBittorrent torrent counts by state and activity.",
    "- total, complete, incomplete (int)\n"
    "- by_state (object): state name to count\n"
    "- with_connected_leechers, actively_uploading, actively_downloading (int)",
)
def qbt_torrents_summary(tag: TagFilter = None, category: CategoryFilter = None) -> str:
    return qbt.torrents_summary(settings, tag=tag, category=category)


@tool(
    "Paginated qBittorrent torrent list with optional tag and state filters.",
    "- total (int): matching torrent count before pagination\n"
    "- limit, offset (int)\n"
    "- torrents (array): hash, name, progress, state, category, tags, ratio, "
    "num_leechs, num_seeds, upspeed_bps, dlspeed_bps, content_path",
)
def qbt_torrents_list(
    tag: TagFilter = None,
    state: TorrentStateFilter = None,
    limit: TorrentListLimit = 25,
    offset: TorrentListOffset = 0,
) -> str:
    return qbt.torrents_list(settings, tag=tag, state=state, limit=limit, offset=offset)


@tool(
    "Detailed view of one qBittorrent torrent including trackers and peers.",
    "- hash, name, state, progress, category, tags, ratio (mixed)\n"
    "- num_leechs, num_seeds, upspeed_bps, dlspeed_bps, content_path\n"
    "- trackers (array): url, status, num_leeches, num_seeds, msg\n"
    "- peers (array): ip, client, up_speed, dl_speed, progress",
)
def qbt_torrent_detail(hash_prefix: HashPrefix) -> str:
    return qbt.torrent_detail(settings, hash_prefix=hash_prefix)


@tool(
    "Active Sonarr download queue snapshot.",
    "- service (str): sonarr\n"
    "- total, page, page_size (int)\n"
    "- records (array): title, status, size, estimatedCompletionTime, etc.",
)
def sonarr_queue_snapshot() -> str:
    return media.sonarr_queue_snapshot(settings)


@tool(
    "Active Radarr download queue snapshot.",
    "- service (str): radarr\n"
    "- total, page, page_size (int)\n"
    "- records (array): title, status, size, estimatedCompletionTime, etc.",
)
def radarr_queue_snapshot() -> str:
    return media.radarr_queue_snapshot(settings)


@tool(
    "Radarr and Sonarr enabled indexer counts (Prowlarr sync preflight).",
    "- radarr, sonarr (object): enabled_indexers, healthy\n"
    "- healthy (bool), hint (str|null)",
)
def arr_indexer_sync_health() -> str:
    return media.arr_indexer_sync_health(settings)


@tool(
    "Ranked Radarr releases for a library movie.",
    "- movie (object): id, title\n"
    "- options (array): rank, title, seeders, size_bytes, guid, movie_id, "
    "indexer_flags, is_freeleech, rejections",
)
def radarr_release_search(query: MediaSearchQuery, movie_id: MovieIdFilter = None) -> str:
    return media.radarr_release_search(settings, query=query, movie_id=movie_id)


@tool(
    "Ranked Sonarr releases for a library series season.",
    "- series (object): id, title, season\n"
    "- options (array): rank, title, seeders, size_bytes, guid, episode_id, "
    "indexer_flags, is_freeleech, rejections",
)
def sonarr_release_search(
    query: MediaSearchQuery,
    season: SeasonNumber,
    series_id: SeriesIdFilter = None,
    include_full_series_packs: IncludeSeriesPacks = False,
) -> str:
    return media.sonarr_release_search(
        settings,
        query=query,
        season=season,
        series_id=series_id,
        include_full_series_packs=include_full_series_packs,
    )


@tool(
    "Direct Prowlarr indexer search for non-library grabs.",
    "- options (array): rank, title, seeders, size_bytes, guid, indexer_id, release, "
    "indexer_flags, is_freeleech, rejections",
)
def prowlarr_indexer_search(
    query: MediaSearchQuery,
    search_type: ProwlarrSearchType = "search",
    limit: ReleaseSearchLimit | None = None,
    indexer_ids: IndexerIdsFilter = None,
) -> str:
    return media.prowlarr_indexer_search(
        settings,
        query=query,
        search_type=search_type,
        limit=limit,
        indexer_ids=indexer_ids,
    )


@tool(
    "Prowlarr indexer health summary.",
    "- total, enabled_count, disabled_count (int)\n"
    "- indexers (array): id, name, enabled, priority, protocol, implementation, status, tags",
)
def prowlarr_indexer_health() -> str:
    return media.prowlarr_indexer_health(settings)


@tool(
    "Overseerr media request counts by status.",
    "- pending, approved, processing, available, declined (int)\n"
    "- open_count (int): pending + approved + processing",
)
def overseerr_request_summary() -> str:
    return media.overseerr_request_summary(settings)


@tool(
    "Trace recent Overseerr requests through Sonarr/Radarr handoff into qBittorrent.",
    "- lookback_hours, limit (int)\n"
    "- summary: total, by_stage, qbt_present, missing_qbt\n"
    "- requests (array): request metadata, stage, diagnosis, arr evidence, qbt match",
)
def overseerr_request_pipeline(
    lookback_hours: OverseerrLookbackHours = 72,
    limit: OverseerrRequestLimit = 50,
) -> str:
    return media.overseerr_request_pipeline(
        settings,
        lookback_hours=lookback_hours,
        limit=limit,
    )


@tool(
    "Cached Seerr request diagnosis from the reconciler worker state.",
    "- requests (array): request id, title, stable ids, state, reason, evidence, candidates, recommended_action, expected_action\n"
    "- reconciler (object): state/status paths, latest worker status, health/freshness\n"
    "- request_id, title, lookback_hours (echo/filter)",
)
def seerr_request_diagnosis(
    request_id: SeerrRequestId = None,
    lookback_hours: SeerrDiagnosisLookbackHours = 24,
    title: SeerrDiagnosisTitle = None,
) -> str:
    return media.seerr_request_diagnosis(
        settings,
        request_id=request_id,
        lookback_hours=lookback_hours,
        title=title,
    )


@tool(
    "Missing subtitles summary from Bazarr by language.",
    "- series_scanned, movies_scanned (int)\n"
    "- missing_by_language (object): language code to count\n"
    "- samples (array): representative missing subtitle rows",
)
def bazarr_missing_subtitles_summary(scan_limit: BazarrScanLimit = 500) -> str:
    return media.bazarr_missing_subtitles_summary(settings, scan_limit=scan_limit)


@tool(
    "NVMe and HDD media path disk usage summary.",
    "- paths (array): path, total_bytes, used_bytes, free_bytes, used_percent",
)
def media_paths_usage() -> str:
    return media.media_paths_usage(settings)


@tool(
    "Verify qBittorrent peer port configuration against expected host port.",
    "- connection_status (str)\n"
    "- configured_port, expected_host_port (int|null)\n"
    "- ports_match (bool|null), host_listener_verified (null in v1.1)\n"
    "- healthy (bool), hint (str)",
)
def qbt_peer_port_verify() -> str:
    return qbt.peer_port_verify(settings)


@tool(
    "List completed NVMe torrents eligible for HDD offload (read-only dry run).",
    "- count, limit (int)\n"
    "- candidates (array): hash, name, content_path, destination_path, state",
)
def qbt_offload_candidates(limit: OffloadLimit = 10) -> str:
    return mut_qbt.offload_candidates(settings, limit=limit)


@tool(
    "Read exclusive qBittorrent download session status from homelab-qbt-events.",
    "- active (bool): whether an exclusive session is running\n"
    "- session (object|null): session_id, name_contains, priority_count, remaining_count, paused_count",
)
def qbt_exclusive_status() -> str:
    return mut_qbt.qbt_exclusive_status(settings)


@tool(
    "Resolve movie or series candidates with library membership (Radarr/Sonarr lookup).",
    "- media_type (str): movie or series\n"
    "- query (str): title or id term\n"
    "- candidates (array): title, year, external ids, in_library, library_id, has_file",
)
def arr_lookup(media_type: MediaType, query: ArrQuery) -> str:
    return media.arr_lookup(settings, media_type=media_type, query=query)


@tool(
    "Library item acquire status: files, queue, and recent history.",
    "- media_type (str): movie or series\n"
    "- library_id (int), title (str)\n"
    "- has_file (bool), quality (str|null)\n"
    "- queue (array): id, title, status, sizeleft, download_id\n"
    "- history (array): eventType, sourceTitle, date",
)
def arr_status(
    media_type: MediaType,
    library_id: LibraryId = None,
    query: ArrQuery | None = None,
    season: ArrSeason = None,
) -> str:
    return media.arr_status(
        settings,
        media_type=media_type,
        library_id=library_id,
        query=query,
        season=season,
    )


@tool(
    "Plan staging a requested movie or series scope onto NVMe for faster playback (read-only).",
    "- media_type, library_id, title, scope, selection\n"
    "- files, paths, destinations, total_size, nvme_free_bytes\n"
    "- qbt_torrents, blockers, warnings, ready, expected_action\n"
    "- mutation (bool): false",
)
def media_title_to_nvme_plan(
    media_type: MediaType,
    query: MediaSearchQueryOptional = None,
    library_id: LibraryId = None,
    scope: MediaStageScope = None,
    season: ArrSeason = None,
    episode: EpisodeNumber = None,
) -> str:
    return mut_qbt.media_title_to_nvme_plan(
        settings,
        media_type=media_type,
        query=query,
        library_id=library_id,
        scope=scope,
        season=season,
        episode=episode,
    )


@tool(
    "Report current read-only staging status for a requested movie or series scope.",
    "- media_type, library_id, scope, selection\n"
    "- staged (bool), paths, qbt_torrents, blockers, warnings\n"
    "- mutation (bool): false",
)
def media_title_to_nvme_status(
    media_type: MediaType,
    query: MediaSearchQueryOptional = None,
    library_id: LibraryId = None,
    scope: MediaStageScope = None,
    season: ArrSeason = None,
    episode: EpisodeNumber = None,
) -> str:
    return mut_qbt.media_title_to_nvme_status(
        settings,
        media_type=media_type,
        query=query,
        library_id=library_id,
        scope=scope,
        season=season,
        episode=episode,
    )


@tool(
    "Search the local Plex library for a title+year+kind match",
    "- sorted list of matched titles with file_paths, year, kind",
)
def plex_library_search(
    title: str,
    year: int | None = None,
    kind: Literal["any", "movie", "show"] = "any",
) -> str:
    return plex_search_bound(title=title, year=year, kind=kind)


@tool(
    "Diagnose whether a given title exists in Plex and/or in ARRs",
    "- VisibilityStage + in_plex matches + arr side matches + notes",
)
def media_visibility_diagnosis(
    title: str,
    year: int | None = None,
    kind: Literal["any", "movie", "show"] = "any",
) -> str:
    return plex_visibility_bound(title=title, year=year, kind=kind)


@tool(
    "Report what a Plex user is watching now and optional show progress",
    "- username (str): Plex display name\n"
    "- active_session (object|null): current playback state\n"
    "- recent_activity (object|null): most recent watch when not actively playing\n"
    "- show_progress (object|null): watched/in-progress episodes when show is provided",
)
def plex_watch_status(
    username: str,
    show: str | None = None,
) -> str:
    return plex_watch_status_bound(username=username, show=show)


@tool(
    "autobrr daily grab stats, IRC status, quota usage, and recent releases.",
    "- filters (array): name, enabled, max_downloads, max_downloads_unit\n"
    "- irc (array): name, connected, healthy, auth_mechanism, channels, connection_errors\n"
    "- recent (array): name, status, filter, rejection_message, timestamp\n"
    "- quota (array): per-filter daily usage against max_downloads",
)
def autobrr_operational_summary(recent_limit: AutobrrRecentLimit = 10) -> str:
    return autobrr.operational_summary(settings, recent_limit=recent_limit)


@tool(
    "Composite autobrr pipeline diagnosis when expected grabs are missing.",
    "- summary (object): same shape as autobrr_operational_summary data\n"
    "- health (object): autobrr and qbittorrent reachable/status, plus extension checks\n"
    "- push_verify (object): recent approved pushes checked against qBittorrent by hash\n"
    "- likely_causes (array of str): e.g. DAILY_QUOTA_EXHAUSTED, IRC_DISCONNECTED\n"
    "- recommendations (array of str): actionable next steps",
)
def autobrr_pipeline_diagnosis() -> str:
    return autobrr.pipeline_diagnosis(settings, checks=autobrr_pipeline_checks)


@tool(
    "List a directory under the media roots (/downloads, /movies, /tv); omit path to list roots.",
    "- path (str|null)\n"
    "- entries (array): name, type (file|dir|symlink), size_bytes, mtime\n"
    "- truncated (bool)",
)
def media_files_list(path: MediaPathOptional = None, limit: FilesListLimit = 200) -> str:
    return media_files.files_list(settings, path=path, limit=limit)


@tool(
    "List soft-deleted media waiting in each root's .homelab-trash.",
    "- entries (array): name, type, size_bytes, mtime, root\n- count, total_bytes (int)",
)
def media_trash_list() -> str:
    return media_files.trash_list(settings)


if settings.enable_mutations:
    @tool(
        "Resume qBittorrent torrents by tag or hash list (tier-2; requires confirm=true).",
        "- mutation (bool): true\n- applied (object): resumed_count",
    )
    def qbt_torrents_resume(
        tag: ResumeTag = None,
        hashes: TorrentHashes = None,
        confirm: ConfirmFlag = False,
    ) -> str:
        return mut_qbt.torrents_resume(
            settings, tag=tag, hashes=hashes, confirm=confirm
        )

    @tool(
        "Apply a qBittorrent preference profile (tier-1).",
        "- mutation (bool): true\n- applied (object): profile, preferences",
    )
    def qbt_apply_profile(
        profile: QbtProfileName,
        expected_action: ExpectedAction = None,
    ) -> str:
        return mut_qbt.apply_profile(
            settings,
            profile=profile,
            expected_action=expected_action,
            extension_profiles=extension_qbt_profiles,
        )

    @tool(
        "Pause qBittorrent torrents by tag or hash list (tier-1; expected_action required).",
        "- mutation (bool): true\n- applied (object): paused_count",
    )
    def qbt_torrents_pause(
        tag: PauseTag = None,
        hashes: TorrentHashes = None,
        expected_action: ExpectedAction = None,
    ) -> str:
        return mut_qbt.torrents_pause(
            settings,
            tag=tag,
            hashes=hashes,
            expected_action=expected_action,
        )

    @tool(
        "Execute NVMe to HDD offload for completed torrents (tier-1; expected_action required).",
        "- mutation (bool): true\n- applied (object): limit, moved_count, results",
    )
    def qbt_offload_run(
        limit: OffloadLimit = 1, expected_action: ExpectedAction = None
    ) -> str:
        return mut_qbt.offload_run(
            settings, limit=limit, expected_action=expected_action
        )

    @tool(
        "Grab a Radarr release (tier-1; expected_action required).",
        "- mutation (bool): true\n- applied (object): app, command",
    )
    def radarr_release_grab(
        movie_id: GrabMovieId,
        guid: GrabGuid,
        size_bytes: GrabSizeBytes,
        indexer_id: GrabIndexerId | None = None,
        expected_action: ExpectedAction = None,
        title: GrabTitle = None,
    ) -> str:
        return mut_media_downloads.radarr_release_grab(
            settings,
            movie_id=movie_id,
            guid=guid,
            size_bytes=size_bytes,
            indexer_id=indexer_id,
            expected_action=expected_action,
            title=title,
            release_guards=release_grab_guards,
        )

    @tool(
        "Grab a Sonarr release (tier-1; expected_action required).",
        "- mutation (bool): true\n- applied (object): app, command",
    )
    def sonarr_release_grab(
        episode_id: GrabEpisodeId,
        guid: GrabGuid,
        size_bytes: GrabSizeBytes,
        expected_action: ExpectedAction = None,
        title: GrabTitle = None,
    ) -> str:
        return mut_media_downloads.sonarr_release_grab(
            settings,
            episode_id=episode_id,
            guid=guid,
            size_bytes=size_bytes,
            expected_action=expected_action,
            title=title,
        )

    @tool(
        "Grab a Prowlarr indexer release (tier-1; expected_action required).",
        "- mutation (bool): true\n- applied (object): source, prowlarr",
    )
    def prowlarr_indexer_grab(
        indexer_id: GrabIndexerId,
        guid: GrabGuid,
        size_bytes: GrabSizeBytes,
        expected_action: ExpectedAction = None,
        title: GrabTitle = None,
    ) -> str:
        return mut_media_downloads.prowlarr_indexer_grab(
            settings,
            indexer_id=indexer_id,
            guid=guid,
            size_bytes=size_bytes,
            expected_action=expected_action,
            title=title,
        )

    @tool(
        "Monitor a Sonarr season (and its episodes) then queue SeasonSearch (tier-1; expected_action required).",
        "- mutation (bool): true\n"
        "- series (object): id, title\n"
        "- season (int)\n"
        "- episodes_monitored (int)\n"
        "- command (object): SeasonSearch id/status\n"
        "- expected_action (str): sonarr-season-ensure:{series_id}:{season}",
    )
    def sonarr_season_ensure_search(
        season: SeasonNumber,
        series_id: SeriesIdFilter = None,
        query: MediaSearchQueryOptional = None,
        expected_action: ExpectedAction = None,
    ) -> str:
        return mut_media_downloads.sonarr_season_ensure_search(
            settings,
            season_number=season,
            series_id=series_id,
            query=query,
            expected_action=expected_action,
        )

    @tool(
        "Add or monitor a movie/series season in Radarr/Sonarr without auto-search (tier-1).",
        "- mutation (bool): true\n"
        "- media_type (str), library_id (int), created (bool), already_existed (bool)\n"
        "- path (str), monitored (bool), season (int, series only)\n"
        "- expected_action (str): ensure:movie:{tmdb_id} or ensure:series:{id}:s{season}",
    )
    def arr_ensure(
        media_type: MediaType,
        tmdb_id: TmdbId = None,
        tvdb_id: TvdbId = None,
        library_id: LibraryId = None,
        season: ArrSeason = None,
        expected_action: ExpectedAction = None,
    ) -> str:
        return mut_media_downloads.arr_ensure(
            settings,
            media_type=media_type,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            library_id=library_id,
            season=season,
            expected_action=expected_action,
        )

    @tool(
        "Remove an Arr queue item (tier-2; confirm=true).",
        "- mutation (bool): true\n"
        "- removed (bool): true\n"
        "- queue_id (int)\n"
        "- remove_from_client (bool), blocklist (bool)",
    )
    def arr_queue_remove(
        media_type: MediaType,
        queue_id: QueueId,
        remove_from_client: bool = True,
        blocklist: bool = False,
        confirm: ConfirmFlag = False,
    ) -> str:
        return mut_media_downloads.arr_queue_remove(
            settings,
            media_type=media_type,
            queue_id=queue_id,
            remove_from_client=remove_from_client,
            blocklist=blocklist,
            confirm=confirm,
        )

    @tool(
        "Force-start a qBittorrent torrent to the front of the download queue (tier-2; confirm=true).",
        "- mutation (bool): true\n"
        "- hash (str)\n"
        "- name (str)\n"
        "- before_state (str)\n"
        "- after_state (str)",
    )
    def qbt_torrent_force_start(
        torrent_hash: TorrentHashSingle = None,
        name_contains: TorrentNameContains = None,
        confirm: ConfirmFlag = False,
    ) -> str:
        return mut_qbt.qbt_torrent_force_start(
            settings,
            torrent_hash=torrent_hash,
            name_contains=name_contains,
            confirm=confirm,
        )

    @tool(
        "Start an exclusive name-match download session via homelab-qbt-events (tier-2; confirm=true).",
        "- mutation (bool): true\n"
        "- session_id (str)\n"
        "- name_contains (str)\n"
        "- priority_hashes, paused_hashes (arrays)\n"
        "- priority_count, paused_count (int)",
    )
    def qbt_exclusive_prioritize(
        name_contains: NameContains,
        confirm: ConfirmFlag = False,
    ) -> str:
        return mut_qbt.qbt_exclusive_prioritize(
            settings,
            name_contains=name_contains,
            confirm=confirm,
        )

    @tool(
        "Cancel the active exclusive download session and resume paused torrents (tier-2; confirm=true).",
        "- mutation (bool): true\n"
        "- resumed_count (int)\n"
        "- resumed_hashes (array)\n"
        "- session_id (str)",
    )
    def qbt_exclusive_cancel(confirm: ConfirmFlag = False) -> str:
        return mut_qbt.qbt_exclusive_cancel(settings, confirm=confirm)



    @tool(
        "Enable or disable an autobrr filter (disable is tier-1 expected_action; enable needs confirm).",
        "- mutation (bool): true\n- applied (object): id, name, enabled",
    )
    def autobrr_filter_set_enabled(
        filter_name: FilterName,
        enabled: FilterEnabled,
        confirm: ConfirmFlag = False,
        expected_action: ExpectedAction = None,
    ) -> str:
        return mut_autobrr.filter_set_enabled(
            settings,
            filter_name=filter_name,
            enabled=enabled,
            confirm=confirm,
            expected_action=expected_action,
        )

    @tool(
        "Restart an autobrr IRC network (tier-2; requires confirm=true).",
        "- mutation (bool): true\n- applied (object): id, name, restarted",
    )
    def autobrr_irc_restart(network: IrcNetworkName, confirm: ConfirmFlag = False) -> str:
        return mut_autobrr.irc_restart(settings, network=network, confirm=confirm)

    @tool(
        "Move or rename a file/directory within or between media roots; never overwrites "
        "(tier-1; expected_action file-move:{src}->{dest}).",
        "- mutation (bool): true\n- applied (object): src, dest",
    )
    def media_file_move(
        src: MediaPath, dest: MediaPath, expected_action: ExpectedAction = None
    ) -> str:
        return mut_files.file_move(settings, src=src, dest=dest, expected_action=expected_action)

    @tool(
        "Soft-delete a media file/directory into its root's .homelab-trash "
        "(tier-1; expected_action file-delete:{path}). Prefer arr_item_delete for library items.",
        "- mutation (bool): true\n- applied (object): path, trash_path",
    )
    def media_file_delete(path: MediaPath, expected_action: ExpectedAction = None) -> str:
        return mut_files.file_delete(settings, path=path, expected_action=expected_action)

    @tool(
        "Permanently purge trash entries older than N days (tier-1; expected_action trash-empty:{days}).",
        "- mutation (bool): true\n- applied (object): removed_count, freed_bytes, removed",
    )
    def media_trash_empty(
        older_than_days: TrashOlderThanDays = 7, expected_action: ExpectedAction = None
    ) -> str:
        return mut_files.trash_empty(
            settings, older_than_days=older_than_days, expected_action=expected_action
        )

    @tool(
        "Delete torrents from qBittorrent, optionally with their data "
        "(tier-1; expected_action qbt-delete:{files|keep-files}:{sorted,hashes}).",
        "- mutation (bool): true\n- applied (object): hashes, delete_files",
    )
    def qbt_torrents_delete(
        hashes: TorrentHashes,
        delete_files: DeleteFiles = False,
        expected_action: ExpectedAction = None,
    ) -> str:
        return mut_library.torrents_delete(
            settings, hashes=hashes or [], delete_files=delete_files, expected_action=expected_action
        )

    @tool(
        "Delete a Radarr movie or Sonarr series, optionally with its files "
        "(tier-1; expected_action arr-delete:{media_type}:{id}:{files|keep-files}).",
        "- mutation (bool): true\n"
        "- applied (object): app, library_id, title, year, path, delete_files, add_exclusion",
    )
    def arr_item_delete(
        media_type: ArrDeleteMediaType,
        library_id: ArrLibraryId,
        delete_files: DeleteFiles = False,
        add_exclusion: AddExclusion = False,
        expected_action: ExpectedAction = None,
    ) -> str:
        return mut_library.arr_item_delete(
            settings,
            media_type=media_type,
            library_id=library_id,
            delete_files=delete_files,
            add_exclusion=add_exclusion,
            expected_action=expected_action,
        )

    @tool(
        "Approve, decline, or retry a Seerr request; approve hands it to Radarr/Sonarr "
        "(tier-2; requires confirm=true).",
        "- mutation (bool): true\n- applied (object): request_id, action, status",
    )
    def seerr_request_action(
        request_id: SeerrRequestIdRequired,
        action: SeerrAction,
        confirm: ConfirmFlag = False,
    ) -> str:
        return mut_library.seerr_request_action(
            settings, request_id=request_id, action=action, confirm=confirm
        )


load_configured_extensions(
    ExtensionContext(
        mcp=mcp,
        settings=settings,
        register_tool=tool,
        capability_descriptors=extension_capability_descriptors,
        release_grab_guards=release_grab_guards,
        tool_roles=extension_tool_roles,
        qbt_profiles=extension_qbt_profiles,
        autobrr_pipeline_checks=autobrr_pipeline_checks,
    )
)
