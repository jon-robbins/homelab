from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil
import time
from typing import Any

import httpx
from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.arr_acquire import arr_lookup as worker_arr_lookup
from homelab_workers.shared.arr_acquire import arr_status as worker_arr_status
from homelab_workers.shared.arr_indexers import arr_indexer_sync_health as worker_arr_indexer_sync_health
from homelab_workers.shared.prowlarr import ProwlarrClient
from homelab_workers.shared.prowlarr_releases import search_prowlarr_releases
from homelab_workers.shared.qbittorrent import QBittorrentClient
from homelab_workers.shared.radarr_releases import ReleaseSearchConfig, search_radarr_releases
from homelab_workers.shared.sonarr_releases import SonarrReleaseSearchConfig, search_sonarr_releases

from homelab_mcp.config import Settings
from homelab_mcp.diagnostics.arr_queue import shape_queue_snapshot
from homelab_mcp.envelope import error_response, ok_response

TOOL_SONARR_QUEUE = "sonarr_queue_snapshot"
TOOL_RADARR_QUEUE = "radarr_queue_snapshot"
TOOL_PROWLARR_INDEXERS = "prowlarr_indexer_health"
TOOL_OVERSEERR_REQUESTS = "overseerr_request_summary"
TOOL_BAZARR_MISSING = "bazarr_missing_subtitles_summary"
TOOL_MEDIA_PATHS = "media_paths_usage"
TOOL_ARR_INDEXER_SYNC = "arr_indexer_sync_health"
TOOL_RADARR_RELEASE_SEARCH = "radarr_release_search"
TOOL_SONARR_RELEASE_SEARCH = "sonarr_release_search"
TOOL_PROWLARR_INDEXER_SEARCH = "prowlarr_indexer_search"
TOOL_ARR_LOOKUP = "arr_lookup"
TOOL_ARR_STATUS = "arr_status"
TOOL_OVERSEERR_PIPELINE = "overseerr_request_pipeline"
TOOL_SEERR_REQUEST_DIAGNOSIS = "seerr_request_diagnosis"


def _upstream_error(tool: str, service: str, exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return error_response(
            tool,
            code="UPSTREAM_TIMEOUT",
            message=f"{service} request timed out",
            hint=f"Verify {service} is reachable from homelab-mcp on homelab_net.",
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else "unknown"
        message = f"{service} request failed: HTTP {status}"
    elif isinstance(exc, httpx.HTTPError):
        message = f"{service} request failed: {exc.__class__.__name__}"
    else:
        message = f"{service} request failed: {exc}"
    return error_response(
        tool,
        code="UPSTREAM_ERROR",
        message=message,
        hint=f"Check {service} credentials and internal URL.",
    )


def _queue_snapshot(settings: Settings, *, tool: str, service: str, base_url: str, api_key: str) -> str:
    started = time.perf_counter()
    try:
        with ArrClient(base_url, api_key, timeout_seconds=15) as client:
            queue = client.queue(page=1, page_size=50)
        data = shape_queue_snapshot(queue, service=service, page_size=50)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(tool, service, exc)


def sonarr_queue_snapshot(settings: Settings) -> str:
    return _queue_snapshot(
        settings,
        tool=TOOL_SONARR_QUEUE,
        service="sonarr",
        base_url=settings.sonarr_url,
        api_key=settings.sonarr_api_key,
    )


def radarr_queue_snapshot(settings: Settings) -> str:
    return _queue_snapshot(
        settings,
        tool=TOOL_RADARR_QUEUE,
        service="radarr",
        base_url=settings.radarr_url,
        api_key=settings.radarr_api_key,
    )


def prowlarr_indexer_health(settings: Settings) -> str:
    started = time.perf_counter()
    tool = TOOL_PROWLARR_INDEXERS
    try:
        with ProwlarrClient(settings.prowlarr_url, settings.prowlarr_api_key) as client:
            rows = client.indexers()
        indexers: list[dict[str, object]] = []
        enabled_count = 0
        disabled_count = 0
        for row in rows:
            is_enabled = bool(row.get("enable"))
            if is_enabled:
                enabled_count += 1
            else:
                disabled_count += 1
            item = {
                "id": row.get("id"),
                "name": row.get("name"),
                "enabled": is_enabled,
                "priority": row.get("priority"),
                "protocol": row.get("protocol"),
                "implementation": row.get("implementation"),
                "indexer_test_not_run": True,
            }
            if "status" in row:
                item["status"] = row.get("status")
            if "tags" in row:
                item["tags"] = row.get("tags")
            indexers.append(item)
        data = {
            "total": len(indexers),
            "enabled_count": enabled_count,
            "disabled_count": disabled_count,
            "indexers": indexers,
        }
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(tool, "prowlarr", exc)


def overseerr_request_summary(settings: Settings) -> str:
    started = time.perf_counter()
    tool = TOOL_OVERSEERR_REQUESTS
    url = f"{settings.overseerr_url.rstrip('/')}/api/v1/request/count"
    headers = (
        {"X-Api-Key": settings.overseerr_api_key} if settings.overseerr_api_key else {}
    )
    try:
        with httpx.Client(timeout=15.0, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            payload = resp.json()
        if not isinstance(payload, dict):
            payload = {}
        data = {
            "pending": int(payload.get("pending") or 0),
            "approved": int(payload.get("approved") or 0),
            "processing": int(payload.get("processing") or 0),
            "available": int(payload.get("available") or 0),
            "declined": int(payload.get("declined") or 0),
        }
        data["open_count"] = data["pending"] + data["approved"] + data["processing"]
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(tool, "overseerr", exc)


def _is_info_hash(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    return len(text) >= 32 and all(char in "0123456789abcdef" for char in text)


def _add_hash(out: list[str], seen: set[str], value: object) -> None:
    if not _is_info_hash(value):
        return
    text = str(value).strip().lower()
    if text not in seen:
        seen.add(text)
        out.append(text)


def _download_hashes_from_row(row: dict[str, Any]) -> list[str]:
    hashes: list[str] = []
    seen: set[str] = set()
    for key in ("downloadId", "trackedDownloadId", "downloadClientId"):
        _add_hash(hashes, seen, row.get(key))
    data = row.get("data")
    if isinstance(data, dict):
        for key in ("downloadId", "downloadClientId", "torrentInfoHash"):
            _add_hash(hashes, seen, data.get(key))
    return hashes


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _int_or_none(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _overseerr_results(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("results") or payload.get("requests") or []
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _request_media(row: dict[str, Any]) -> dict[str, Any]:
    media = row.get("media")
    if isinstance(media, dict):
        return media
    media = row.get("mediaInfo")
    return media if isinstance(media, dict) else {}


def _request_media_type(row: dict[str, Any], media: dict[str, Any]) -> str:
    raw = row.get("type") or media.get("mediaType") or media.get("type") or ""
    text = str(raw).lower()
    if text in {"movie", "tv"}:
        return text
    return "unknown"


def _arr_id(media_type: str, media: dict[str, Any]) -> int | None:
    keys = (
        ("externalServiceId", "radarrId", "serviceId")
        if media_type == "movie"
        else ("externalServiceId", "sonarrId", "serviceId")
    )
    for key in keys:
        value = _int_or_none(media.get(key))
        if value:
            return value
    return None


def _queue_rows_for(queue: dict[str, Any], media_type: str, arr_id: int) -> list[dict[str, Any]]:
    id_field = "movieId" if media_type == "movie" else "seriesId"
    rows: list[dict[str, Any]] = []
    for row in queue.get("records") or []:
        if not isinstance(row, dict):
            continue
        if _int_or_none(row.get(id_field)) == arr_id:
            rows.append(row)
    return rows


def _history_events(client: ArrClient, media_type: str, arr_id: int) -> list[dict[str, Any]]:
    query_key = "movieIds" if media_type == "movie" else "seriesIds"
    payload = client.get("/history", {query_key: arr_id, "pageSize": 20})
    if not isinstance(payload, dict):
        return []
    return [row for row in payload.get("records") or [] if isinstance(row, dict)]


def _event_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventType": row.get("eventType"),
        "sourceTitle": row.get("sourceTitle"),
        "date": row.get("date"),
    }


def _qbt_summary(torrent: dict[str, Any]) -> dict[str, Any]:
    return {
        "hash": str(torrent.get("hash") or "").lower(),
        "name": torrent.get("name"),
        "category": torrent.get("category"),
        "state": torrent.get("state"),
        "progress": float(torrent.get("progress") or 0),
    }


def _stage_for(
    *,
    arr_id: int | None,
    hashes: list[str],
    qbt_match: dict[str, Any] | None,
    history: list[dict[str, Any]],
) -> tuple[str, str | None]:
    if qbt_match:
        progress = float(qbt_match.get("progress") or 0)
        return ("qbt_complete" if progress >= 0.999 else "qbt_active", None)
    imported_events = {"downloadFolderImported", "movieFileImported", "episodeFileImported"}
    if any(str(row.get("eventType") or "") in imported_events for row in history):
        return "arr_imported", None
    if hashes:
        return (
            "arr_grabbed_missing_qbt",
            "arr has a qBT download hash but qBT has no matching torrent",
        )
    if arr_id:
        return "arr_known_no_grab", "arr item exists but no qBT download hash was found"
    return "seerr_only", "request is not linked to a Sonarr/Radarr item"


def overseerr_request_pipeline(
    settings: Settings, *, lookback_hours: int = 72, limit: int = 50
) -> str:
    started = time.perf_counter()
    tool = TOOL_OVERSEERR_PIPELINE
    if lookback_hours <= 0 or lookback_hours > 720:
        return error_response(
            tool,
            code="INVALID_ARGUMENT",
            message="lookback_hours must be 1..720",
        )
    if limit <= 0 or limit > 200:
        return error_response(tool, code="INVALID_ARGUMENT", message="limit must be 1..200")

    try:
        headers = {"X-Api-Key": settings.overseerr_api_key} if settings.overseerr_api_key else {}
        with httpx.Client(timeout=20.0, headers=headers) as client:
            response = client.get(
                f"{settings.overseerr_url.rstrip('/')}/api/v1/request",
                params={"take": limit, "skip": 0},
            )
            response.raise_for_status()
            request_rows = _overseerr_results(response.json())

        cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
        filtered_rows = [
            row
            for row in request_rows
            if (_parse_time(row.get("createdAt") or row.get("updatedAt")) or cutoff) >= cutoff
        ]

        with ArrClient(settings.sonarr_url, settings.sonarr_api_key, timeout_seconds=15) as sonarr:
            sonarr_queue = sonarr.queue(page=1, page_size=50)
            with ArrClient(
                settings.radarr_url,
                settings.radarr_api_key,
                timeout_seconds=15,
            ) as radarr:
                radarr_queue = radarr.queue(page=1, page_size=50)
                with QBittorrentClient(
                    settings.qbt_url,
                    settings.qbt_username,
                    settings.qbt_password,
                    timeout_seconds=15,
                ) as qbt:
                    qbt_by_hash = {
                        str(row.get("hash") or "").lower(): row
                        for row in qbt.torrents_info_raw()
                        if row.get("hash")
                    }

                requests: list[dict[str, Any]] = []
                for row in filtered_rows:
                    media = _request_media(row)
                    media_type = _request_media_type(row, media)
                    arr_id = _arr_id(media_type, media) if media_type in {"movie", "tv"} else None
                    arr_service = None
                    if media_type == "movie":
                        arr_service = "radarr"
                    elif media_type == "tv":
                        arr_service = "sonarr"
                    queue_rows: list[dict[str, Any]] = []
                    history_rows: list[dict[str, Any]] = []
                    if arr_id and media_type == "movie":
                        queue_rows = _queue_rows_for(radarr_queue, media_type, arr_id)
                        history_rows = _history_events(radarr, media_type, arr_id)
                    elif arr_id and media_type == "tv":
                        queue_rows = _queue_rows_for(sonarr_queue, media_type, arr_id)
                        history_rows = _history_events(sonarr, media_type, arr_id)

                    hashes: list[str] = []
                    seen: set[str] = set()
                    for source_row in [*queue_rows, *history_rows]:
                        for hash_value in _download_hashes_from_row(source_row):
                            _add_hash(hashes, seen, hash_value)

                    qbt_match = next((qbt_by_hash[h] for h in hashes if h in qbt_by_hash), None)
                    stage, diagnosis = _stage_for(
                        arr_id=arr_id,
                        hashes=hashes,
                        qbt_match=qbt_match,
                        history=history_rows,
                    )
                    requester = row.get("requestedBy")
                    requests.append(
                        {
                            "request_id": row.get("id"),
                            "media_type": media_type,
                            "title": media.get("title") or media.get("name"),
                            "created_at": row.get("createdAt"),
                            "requester": requester.get("displayName")
                            if isinstance(requester, dict)
                            else None,
                            "stage": stage,
                            "diagnosis": diagnosis,
                            "arr": {
                                "service": arr_service,
                                "id": arr_id,
                                "queue_count": len(queue_rows),
                                "history": [_event_summary(h) for h in history_rows[:5]],
                                "download_hashes": hashes,
                            },
                            "qbt": _qbt_summary(qbt_match) if qbt_match else None,
                        }
                    )

        by_stage = Counter(str(item["stage"]) for item in requests)
        data = {
            "lookback_hours": lookback_hours,
            "limit": limit,
            "summary": {
                "total": len(requests),
                "by_stage": dict(by_stage),
                "qbt_present": by_stage["qbt_active"] + by_stage["qbt_complete"],
                "missing_qbt": by_stage["arr_grabbed_missing_qbt"],
            },
            "requests": requests,
        }
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(tool, "overseerr/arr/qbt", exc)


def _diagnose_recent_seerr_requests(
    settings: Settings,
    *,
    request_id: int | None,
    lookback_hours: int,
    title: str | None,
) -> list[dict[str, Any]]:
    from homelab_workers.shared.seerr_reconciler_state import read_reconciler_state

    state = read_reconciler_state(settings.seerr_reconciler_state_path)
    requests = state.get("requests") if isinstance(state, dict) else {}
    rows: list[dict[str, Any]] = []
    cutoff = int(time.time()) - (lookback_hours * 3600)
    if isinstance(requests, dict):
        for key, value in requests.items():
            if not isinstance(value, dict):
                continue
            row = dict(value)
            row.setdefault("state_key", key)
            if request_id is not None and _int_or_none(row.get("request_id")) != request_id:
                continue
            if title and title.lower() not in str(row.get("title") or "").lower():
                continue
            diagnosed_at = _int_or_none(row.get("diagnosed_at"))
            if diagnosed_at is None or diagnosed_at < cutoff:
                continue
            rows.append(row)
    return rows


def _seerr_reconciler_snapshot(settings: Settings) -> dict[str, Any]:
    from homelab_workers.shared.seerr_reconciler_state import (
        evaluate_reconciler_health,
        read_reconciler_state,
    )

    status_path = Path(settings.seerr_reconciler_status_path)
    status = read_reconciler_state(str(status_path)) if status_path.is_file() else None
    max_age = getattr(settings, "seerr_reconciler_status_max_age_seconds", 900)
    return {
        "source": "seerr_reconciler_state",
        "state_path": settings.seerr_reconciler_state_path,
        "status_path": settings.seerr_reconciler_status_path,
        "status": status,
        "health": evaluate_reconciler_health(
            status,
            max_age,
            now=int(time.time()),
        ),
    }


def seerr_request_diagnosis(
    settings: Settings,
    *,
    request_id: int | None = None,
    lookback_hours: int = 24,
    title: str | None = None,
) -> str:
    started = time.perf_counter()
    try:
        rows = _diagnose_recent_seerr_requests(
            settings,
            request_id=request_id,
            lookback_hours=lookback_hours,
            title=title,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(
            TOOL_SEERR_REQUEST_DIAGNOSIS,
            {
                "lookback_hours": lookback_hours,
                "request_id": request_id,
                "title": title,
                "reconciler": _seerr_reconciler_snapshot(settings),
                "requests": rows,
            },
            duration_ms=duration_ms,
        )
    except Exception as exc:
        return _upstream_error(TOOL_SEERR_REQUEST_DIAGNOSIS, "seerr-reconciler", exc)


def _bazarr_get(base: str, key: str, path: str) -> dict[str, Any]:
    url = f"{base.rstrip('/')}{path}"
    with httpx.Client(timeout=30.0, headers={"X-API-KEY": key}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    return payload if isinstance(payload, dict) else {}


def _bazarr_missing_rows(
    base: str, key: str, resource: str, *, scan_limit: int
) -> tuple[int, list[dict[str, Any]], Counter[str]]:
    missing_rows: list[dict[str, Any]] = []
    by_lang: Counter[str] = Counter()
    scanned = 0
    start = 0
    page_size = 100
    total = 0
    while scanned < scan_limit:
        chunk = min(page_size, scan_limit - scanned)
        data = _bazarr_get(base, key, f"/api/{resource}?start={start}&length={chunk}")
        total = int(data.get("total") or 0)
        rows = data.get("data") or []
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            missing = row.get("missing_subtitles") or []
            if not missing:
                continue
            langs: list[str] = []
            for item in missing:
                if isinstance(item, dict):
                    lang = item.get("code2") or item.get("language") or "unknown"
                elif isinstance(item, str):
                    lang = item
                else:
                    lang = "unknown"
                value = str(lang)
                langs.append(value)
                by_lang[value] += 1
            missing_rows.append(
                {
                    "id": row.get("radarrId") or row.get("sonarrSeriesId") or row.get("sonarrEpisodeId"),
                    "title": row.get("title") or row.get("seriesTitle"),
                    "missing_languages": langs,
                }
            )
        scanned += len(rows)
        start += len(rows)
        if scanned >= total:
            break
    return total, missing_rows, by_lang


def bazarr_missing_subtitles_summary(settings: Settings, scan_limit: int = 500) -> str:
    started = time.perf_counter()
    tool = TOOL_BAZARR_MISSING
    if not settings.bazarr_api_key:
        return error_response(
            tool,
            code="INVALID_ARGUMENT",
            message="BAZARR_API_KEY is not configured",
            hint="Set BAZARR_API_KEY in .env and restart homelab-mcp.",
        )
    try:
        movies_total, movie_rows, movie_langs = _bazarr_missing_rows(
            settings.bazarr_url, settings.bazarr_api_key, "movies", scan_limit=scan_limit
        )
        episodes_total, episode_rows, episode_langs = _bazarr_missing_rows(
            settings.bazarr_url, settings.bazarr_api_key, "episodes", scan_limit=scan_limit
        )
        by_language = Counter(movie_langs)
        by_language.update(episode_langs)
        data = {
            "scan_limit_per_type": scan_limit,
            "movies_total": movies_total,
            "episodes_total": episodes_total,
            "movies_with_missing": len(movie_rows),
            "episodes_with_missing": len(episode_rows),
            "missing_by_language": dict(sorted(by_language.items())),
            "sample_movies": movie_rows[:10],
            "sample_episodes": episode_rows[:10],
        }
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(tool, "bazarr", exc)


def media_paths_usage(settings: Settings) -> str:
    started = time.perf_counter()
    tool = TOOL_MEDIA_PATHS
    paths: list[dict[str, object]] = []
    for name, mount_path in (
        ("nvme", settings.media_nvme_mount),
        ("hdd", settings.media_hdd_mount),
    ):
        path = mount_path.rstrip("/") or mount_path
        if not path:
            paths.append({"name": name, "path": path, "status": "skipped", "detail": "path not configured"})
            continue
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            paths.append({"name": name, "path": path, "status": "unavailable", "detail": str(exc)})
            continue
        total = int(usage.total)
        used = int(usage.used)
        free = int(usage.free)
        pct_used = round(100.0 * used / total, 1) if total else 0.0
        paths.append(
            {
                "name": name,
                "path": path,
                "status": "ok",
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": free,
                "used_pct": pct_used,
            }
        )
    duration_ms = int((time.perf_counter() - started) * 1000)
    return ok_response(tool, {"paths": paths}, duration_ms=duration_ms)


def _release_search_config(settings: Settings) -> ReleaseSearchConfig:
    return ReleaseSearchConfig(
        search_timeout_s=settings.release_search_timeout_s,
        poll_s=settings.release_search_poll_s,
        options_limit=min(settings.release_search_limit, 25),
    )


def _worker_payload_error(tool: str, payload: dict[str, Any]) -> str:
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    import json

    body = {
        "ok": False,
        "tool": tool,
        "error": dict(err),
        "meta": {},
    }
    if "code" not in body["error"]:
        body["error"]["code"] = "UPSTREAM_ERROR"
    if "message" not in body["error"]:
        body["error"]["message"] = "request failed"
    return json.dumps(body, separators=(",", ":"))


def arr_indexer_sync_health(settings: Settings) -> str:
    started = time.perf_counter()
    tool = TOOL_ARR_INDEXER_SYNC
    radarr_client: ArrClient | None = None
    sonarr_client: ArrClient | None = None
    try:
        if settings.radarr_api_key:
            radarr_client = ArrClient(
                settings.radarr_url, settings.radarr_api_key, timeout_seconds=15
            )
        if settings.sonarr_api_key:
            sonarr_client = ArrClient(
                settings.sonarr_url, settings.sonarr_api_key, timeout_seconds=15
            )
        data = worker_arr_indexer_sync_health(radarr=radarr_client, sonarr=sonarr_client)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(tool, "arr", exc)
    finally:
        if radarr_client is not None:
            radarr_client.close()
        if sonarr_client is not None:
            sonarr_client.close()


def radarr_release_search(
    settings: Settings,
    *,
    query: str,
    movie_id: int | None = None,
) -> str:
    started = time.perf_counter()
    tool = TOOL_RADARR_RELEASE_SEARCH
    if not settings.radarr_api_key:
        return error_response(
            tool,
            code="RADARR_NOT_CONFIGURED",
            message="RADARR_API_KEY is not configured",
            hint="Set RADARR_API_KEY in .env and restart homelab-mcp.",
        )
    try:
        with ArrClient(settings.radarr_url, settings.radarr_api_key, timeout_seconds=60) as client:
            payload = search_radarr_releases(
                client,
                query=query,
                movie_id=movie_id,
                config=_release_search_config(settings),
            )
        if payload.get("ok") is not True:
            return _worker_payload_error(tool, payload)
        data = {k: v for k, v in payload.items() if k != "ok"}
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(tool, "radarr", exc)


def sonarr_release_search(
    settings: Settings,
    *,
    query: str,
    season: int,
    series_id: int | None = None,
    include_full_series_packs: bool = False,
) -> str:
    started = time.perf_counter()
    tool = TOOL_SONARR_RELEASE_SEARCH
    if not settings.sonarr_api_key:
        return error_response(
            tool,
            code="SONARR_NOT_CONFIGURED",
            message="SONARR_API_KEY is not configured",
            hint="Set SONARR_API_KEY in .env and restart homelab-mcp.",
        )
    try:
        with ArrClient(settings.sonarr_url, settings.sonarr_api_key, timeout_seconds=60) as client:
            payload = search_sonarr_releases(
                client,
                query=query,
                season=season,
                series_id=series_id,
                include_full_series_packs=include_full_series_packs,
                config=SonarrReleaseSearchConfig(
                    search_timeout_s=settings.release_search_timeout_s,
                    poll_s=settings.release_search_poll_s,
                    options_limit=min(settings.release_search_limit, 25),
                ),
            )
        if payload.get("ok") is not True:
            return _worker_payload_error(tool, payload)
        data = {k: v for k, v in payload.items() if k != "ok"}
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(tool, "sonarr", exc)


def prowlarr_indexer_search(
    settings: Settings,
    *,
    query: str,
    search_type: str = "search",
    limit: int | None = None,
    indexer_ids: list[int] | None = None,
) -> str:
    started = time.perf_counter()
    tool = TOOL_PROWLARR_INDEXER_SEARCH
    if not settings.prowlarr_api_key:
        return error_response(
            tool,
            code="PROWLARR_NOT_CONFIGURED",
            message="PROWLARR_API_KEY is not configured",
            hint="Set PROWLARR_API_KEY in .env and restart homelab-mcp.",
        )
    try:
        with ProwlarrClient(settings.prowlarr_url, settings.prowlarr_api_key, timeout_seconds=60) as client:
            payload = search_prowlarr_releases(
                client,
                query=query,
                search_type=search_type,
                limit=limit or settings.release_search_limit,
                indexer_ids=indexer_ids,
            )
        if payload.get("ok") is not True:
            return _worker_payload_error(tool, payload)
        data = {k: v for k, v in payload.items() if k != "ok"}
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(tool, "prowlarr", exc)


def _arr_service_config(settings: Settings, *, media_type: str, tool: str) -> tuple[str, str, str] | str:
    mt = media_type.strip().lower()
    if mt == "movie":
        if not settings.radarr_api_key:
            return error_response(
                tool,
                code="RADARR_NOT_CONFIGURED",
                message="RADARR_API_KEY is not configured",
                hint="Set RADARR_API_KEY in .env and restart homelab-mcp.",
            )
        return (settings.radarr_url, settings.radarr_api_key, "radarr")
    if mt == "series":
        if not settings.sonarr_api_key:
            return error_response(
                tool,
                code="SONARR_NOT_CONFIGURED",
                message="SONARR_API_KEY is not configured",
                hint="Set SONARR_API_KEY in .env and restart homelab-mcp.",
            )
        return (settings.sonarr_url, settings.sonarr_api_key, "sonarr")
    return error_response(
        tool,
        code="INVALID_ARGUMENT",
        message='media_type must be "movie" or "series"',
    )


def arr_lookup(settings: Settings, *, media_type: str, query: str) -> str:
    started = time.perf_counter()
    tool = TOOL_ARR_LOOKUP
    resolved = _arr_service_config(settings, media_type=media_type, tool=tool)
    if isinstance(resolved, str):
        return resolved
    base_url, api_key, service = resolved
    try:
        with ArrClient(base_url, api_key, timeout_seconds=60) as client:
            payload = worker_arr_lookup(client, media_type=media_type, query=query)
        if payload.get("ok") is not True:
            return _worker_payload_error(tool, payload)
        data = {k: v for k, v in payload.items() if k != "ok"}
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(tool, service, exc)


def arr_status(
    settings: Settings,
    *,
    media_type: str,
    library_id: int | None = None,
    query: str | None = None,
    season: int | None = None,
) -> str:
    started = time.perf_counter()
    tool = TOOL_ARR_STATUS
    resolved = _arr_service_config(settings, media_type=media_type, tool=tool)
    if isinstance(resolved, str):
        return resolved
    base_url, api_key, service = resolved
    try:
        with ArrClient(base_url, api_key, timeout_seconds=60) as client:
            payload = worker_arr_status(
                client,
                media_type=media_type,
                library_id=library_id,
                query=query,
                season=season,
            )
        if payload.get("ok") is not True:
            return _worker_payload_error(tool, payload)
        data = {k: v for k, v in payload.items() if k != "ok"}
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        return _upstream_error(tool, service, exc)
