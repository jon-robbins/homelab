from __future__ import annotations

import time
from typing import Any

import httpx
from homelab_workers.shared.profile_hint import infer_qbt_profile_hint
from homelab_workers.shared.qbittorrent import QBittorrentClient

from homelab_mcp.config import Settings
from homelab_mcp.envelope import error_response, ok_response
from homelab_mcp.redaction import tracker_url_summary

TOOL_TRANSFER_SUMMARY = "qbt_transfer_summary"
TOOL_PREFERENCES_SNAPSHOT = "qbt_preferences_snapshot"
TOOL_TORRENTS_SUMMARY = "qbt_torrents_summary"
TOOL_TORRENTS_LIST = "qbt_torrents_list"
TOOL_TORRENT_DETAIL = "qbt_torrent_detail"
TOOL_PEER_PORT_VERIFY = "qbt_peer_port_verify"


def _qbt_client(settings: Settings) -> QBittorrentClient:
    return QBittorrentClient(
        settings.qbt_url, settings.qbt_username, settings.qbt_password
    )


def _upstream_error(tool: str, started: float, exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return error_response(
            tool,
            code="UPSTREAM_TIMEOUT",
            message="qBittorrent request timed out",
            hint="Verify qBittorrent is reachable and healthy.",
        )
    _ = started
    return error_response(
        tool,
        code="UPSTREAM_ERROR",
        message=f"qBittorrent request failed: {exc}",
        hint="Check qBittorrent credentials and connectivity.",
    )


def transfer_summary(settings: Settings) -> str:
    started = time.perf_counter()
    try:
        with _qbt_client(settings) as client:
            transfer = client.transfer_info()
        data = {
            "connection_status": transfer.get("connection_status"),
            "last_external_address_v4": transfer.get("last_known_external_address"),
            "dl_speed_bps": int(transfer.get("dl_info_speed") or 0),
            "up_speed_bps": int(transfer.get("up_info_speed") or 0),
            "dl_rate_limit_bps": int(transfer.get("dl_rate_limit") or 0),
            "up_rate_limit_bps": int(transfer.get("up_rate_limit") or 0),
            "dht_nodes": int(transfer.get("dht_nodes") or 0),
        }
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(TOOL_TRANSFER_SUMMARY, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(TOOL_TRANSFER_SUMMARY, started, exc)


def preferences_snapshot(
    settings: Settings, *, extension_profiles: dict[str, dict[str, Any]] | None = None
) -> str:
    started = time.perf_counter()
    try:
        with _qbt_client(settings) as client:
            prefs = client.app_preferences()
        keys = (
            "queueing_enabled",
            "max_active_downloads",
            "max_active_uploads",
            "max_connec",
            "max_connec_per_torrent",
            "dl_limit",
            "up_limit",
        )
        data: dict[str, Any] = {key: prefs.get(key) for key in keys}
        data["profile_hint"] = infer_qbt_profile_hint(prefs, extension_profiles)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(TOOL_PREFERENCES_SNAPSHOT, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(TOOL_PREFERENCES_SNAPSHOT, started, exc)


def torrents_summary(
    settings: Settings, tag: str | None = None, category: str | None = None
) -> str:
    started = time.perf_counter()
    try:
        with _qbt_client(settings) as client:
            torrents = client.torrents_info_raw(tag=tag, category=category)
        by_state: dict[str, int] = {}
        complete = 0
        with_connected_leechers = 0
        actively_uploading = 0
        actively_downloading = 0
        for torrent in torrents:
            state = str(torrent.get("state") or "unknown")
            by_state[state] = by_state.get(state, 0) + 1
            progress = float(torrent.get("progress") or 0)
            if progress >= 0.999:
                complete += 1
            if int(torrent.get("num_leechs") or 0) > 0:
                with_connected_leechers += 1
            if float(torrent.get("upspeed") or 0) > 0:
                actively_uploading += 1
            if float(torrent.get("dlspeed") or 0) > 0:
                actively_downloading += 1
        total = len(torrents)
        data = {
            "total": total,
            "by_state": by_state,
            "complete": complete,
            "incomplete": total - complete,
            "with_connected_leechers": with_connected_leechers,
            "actively_uploading": actively_uploading,
            "actively_downloading": actively_downloading,
        }
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(TOOL_TORRENTS_SUMMARY, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(TOOL_TORRENTS_SUMMARY, started, exc)


def torrents_list(
    settings: Settings,
    tag: str | None = None,
    state: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> str:
    if limit <= 0 or limit > 100:
        return error_response(
            TOOL_TORRENTS_LIST,
            code="INVALID_ARGUMENT",
            message="limit must be between 1 and 100",
        )
    if offset < 0:
        return error_response(
            TOOL_TORRENTS_LIST,
            code="INVALID_ARGUMENT",
            message="offset must be >= 0",
        )
    started = time.perf_counter()
    try:
        with _qbt_client(settings) as client:
            torrents = client.torrents_info_raw(tag=tag, filter_name=state)
        page = torrents[offset : offset + limit]
        rows = [
            {
                "hash": torrent.get("hash"),
                "name": torrent.get("name"),
                "progress": float(torrent.get("progress") or 0),
                "state": torrent.get("state"),
                "category": torrent.get("category"),
                "tags": torrent.get("tags"),
                "ratio": float(torrent.get("ratio") or 0),
                "num_leechs": int(torrent.get("num_leechs") or 0),
                "num_seeds": int(torrent.get("num_seeds") or 0),
                "upspeed_bps": int(torrent.get("upspeed") or 0),
                "dlspeed_bps": int(torrent.get("dlspeed") or 0),
                "content_path": torrent.get("content_path"),
            }
            for torrent in page
        ]
        data = {
            "total": len(torrents),
            "limit": limit,
            "offset": offset,
            "torrents": rows,
        }
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(TOOL_TORRENTS_LIST, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(TOOL_TORRENTS_LIST, started, exc)


def torrent_detail(settings: Settings, hash_prefix: str) -> str:
    prefix = hash_prefix.strip().lower()
    if len(prefix) < 8:
        return error_response(
            TOOL_TORRENT_DETAIL,
            code="INVALID_ARGUMENT",
            message="hash_prefix must be at least 8 characters",
            hint="Use qbt_torrents_list to find a valid hash prefix.",
        )

    started = time.perf_counter()
    try:
        with _qbt_client(settings) as client:
            torrents = client.torrents_info_raw()
            matches = [
                torrent
                for torrent in torrents
                if str(torrent.get("hash") or "").lower().startswith(prefix)
            ]
            if len(matches) != 1:
                return error_response(
                    TOOL_TORRENT_DETAIL,
                    code="NOT_FOUND",
                    message=f"No unique torrent with hash prefix {prefix}",
                    hint="Use qbt_torrents_list to locate a unique hash prefix.",
                )
            torrent = matches[0]
            torrent_hash = str(torrent.get("hash") or "")
            trackers_raw = client.torrent_trackers(torrent_hash)
            peers_raw = client.torrent_peers(torrent_hash)

        trackers = [
            {
                **tracker_url_summary(row.get("url")),
                "status": row.get("status"),
                "num_leeches": row.get("num_leeches"),
                "num_seeds": row.get("num_seeds"),
                "msg": row.get("msg"),
            }
            for row in trackers_raw
        ]
        peers = [
            {
                "ip": peer.get("ip"),
                "client": peer.get("client"),
                "up_speed": peer.get("up_speed"),
                "dl_speed": peer.get("dl_speed"),
                "progress": peer.get("progress"),
            }
            for peer in (peers_raw.get("peers") or {}).values()
            if isinstance(peer, dict)
        ]

        data = {
            "hash": torrent.get("hash"),
            "name": torrent.get("name"),
            "state": torrent.get("state"),
            "progress": float(torrent.get("progress") or 0),
            "category": torrent.get("category"),
            "tags": torrent.get("tags"),
            "ratio": float(torrent.get("ratio") or 0),
            "num_leechs": int(torrent.get("num_leechs") or 0),
            "num_seeds": int(torrent.get("num_seeds") or 0),
            "upspeed_bps": int(torrent.get("upspeed") or 0),
            "dlspeed_bps": int(torrent.get("dlspeed") or 0),
            "content_path": torrent.get("content_path"),
            "trackers": trackers,
            "peer_count": len(peers),
            "peers": peers,
        }
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(TOOL_TORRENT_DETAIL, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(TOOL_TORRENT_DETAIL, started, exc)


def peer_port_verify(settings: Settings) -> str:
    started = time.perf_counter()
    try:
        with _qbt_client(settings) as client:
            transfer = client.transfer_info()
            prefs = client.app_preferences()
        configured_port = int(
            prefs.get("listen_port") or prefs.get("session_port") or 0
        )
        expected_port = settings.qbt_peer_port
        connection_status = transfer.get("connection_status")
        ports_match = configured_port == expected_port if configured_port else None
        connected = connection_status == "connected"
        data = {
            "connection_status": connection_status,
            "configured_port": configured_port or None,
            "expected_host_port": expected_port,
            "ports_match": ports_match,
            "host_listener_verified": None,
            "healthy": connected and (ports_match is not False),
            "hint": (
                "Host listener check requires a sidecar probe (v1.1 limitation). "
                "Verify compose publishes QBITTORRENT_PEER_PORT and router forwards TCP+UDP."
            ),
        }
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(TOOL_PEER_PORT_VERIFY, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(TOOL_PEER_PORT_VERIFY, started, exc)
