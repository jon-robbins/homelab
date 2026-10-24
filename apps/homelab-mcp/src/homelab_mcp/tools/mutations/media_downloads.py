from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

import httpx
from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.arr_acquire import arr_ensure as worker_arr_ensure
from homelab_workers.shared.arr_acquire import arr_queue_remove as worker_arr_queue_remove
from homelab_workers.shared.release_formatting import int_field
from homelab_workers.shared.prowlarr import ProwlarrClient
from homelab_workers.shared.prowlarr_releases import grab_prowlarr_release
from homelab_workers.shared.radarr_releases import grab_radarr_release
from homelab_workers.shared.sonarr_releases import (
    ensure_season_monitored_and_search,
    grab_sonarr_release,
    resolve_series_in_library,
)

from homelab_mcp.audit_log import write_audit_record
from homelab_mcp.config import Settings
from homelab_mcp.envelope import error_response, ok_response
from homelab_mcp.mutation_gate import (
    canonical_arr_ensure_movie,
    canonical_arr_ensure_series,
    canonical_grab_prowlarr,
    canonical_grab_radarr,
    canonical_grab_sonarr,
    canonical_season_ensure,
    require_tier1,
    require_tier2,
)

TOOL_RADARR_RELEASE_GRAB = "radarr_release_grab"
TOOL_SONARR_RELEASE_GRAB = "sonarr_release_grab"
TOOL_PROWLARR_INDEXER_GRAB = "prowlarr_indexer_grab"
TOOL_SONARR_SEASON_ENSURE_SEARCH = "sonarr_season_ensure_search"
TOOL_ARR_ENSURE = "arr_ensure"
TOOL_ARR_QUEUE_REMOVE = "arr_queue_remove"


def _check_mutations_enabled(settings: Settings, tool: str) -> str | None:
    if not settings.enable_mutations:
        return error_response(
            tool,
            code="MUTATIONS_DISABLED",
            message="Mutation tools are disabled",
            hint="Set HOMELAB_MCP_ENABLE_MUTATIONS=1 and restart homelab-mcp.",
        )
    return None


def _tier1_gate_response(
    settings: Settings,
    *,
    tool: str,
    started: float,
    expected: str,
    expected_action: str | None,
) -> str | None:
    gate_err = require_tier1(expected=expected, provided=expected_action)
    if not gate_err:
        return None
    duration_ms = int((time.perf_counter() - started) * 1000)
    write_audit_record(
        audit_path=settings.audit_path,
        tool=tool,
        tier=1,
        ok=False,
        duration_ms=duration_ms,
        error_code=gate_err["code"],
    )
    return error_response(
        tool,
        code=gate_err["code"],
        message=gate_err["message"],
        hint=gate_err.get("hint", ""),
    )


def _worker_error(tool: str, payload: dict[str, Any]) -> str:
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    return error_response(
        tool,
        code=str(err.get("code") or "UPSTREAM_ERROR"),
        message=str(err.get("message") or "request failed"),
        hint=str(err.get("hint") or ""),
    )


def _arr_upstream_error(tool: str, service: str, exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return error_response(
            tool,
            code="UPSTREAM_TIMEOUT",
            message=f"{service} request timed out",
        )
    return error_response(
        tool,
        code="UPSTREAM_ERROR",
        message=f"{service} request failed: {exc}",
    )


def radarr_release_grab(
    settings: Settings,
    *,
    movie_id: int,
    guid: str,
    size_bytes: int,
    indexer_id: int | None = None,
    expected_action: str | None = None,
    title: str | None = None,
    release_guards: Iterable[Callable[[int | None, str | None], str | None]] = (),
) -> str:
    started = time.perf_counter()
    tool = TOOL_RADARR_RELEASE_GRAB
    disabled = _check_mutations_enabled(settings, tool)
    if disabled:
        return disabled

    if movie_id <= 0 or size_bytes <= 0 or not guid or not guid.strip():
        return error_response(
            tool,
            code="INVALID_ARGUMENT",
            message="movie_id and size_bytes must be positive integers, guid must be non-empty",
        )

    if indexer_id is not None:
        if indexer_id <= 0:
            return error_response(
                tool,
                code="INVALID_ARGUMENT",
                message="indexer_id must be a positive integer",
            )

    for guard in release_guards:
        rejected = guard(indexer_id, title)
        if rejected:
            return rejected

    if not settings.radarr_api_key:
        return error_response(
            tool,
            code="RADARR_NOT_CONFIGURED",
            message="RADARR_API_KEY is not configured",
            hint="Set RADARR_API_KEY in .env and restart homelab-mcp.",
        )
    expected = canonical_grab_radarr(movie_id, size_bytes)
    gated = _tier1_gate_response(
        settings,
        tool=tool,
        started=started,
        expected=expected,
        expected_action=expected_action,
    )
    if gated:
        return gated
    try:
        with ArrClient(settings.radarr_url, settings.radarr_api_key, timeout_seconds=60) as client:
            payload = grab_radarr_release(client, movie_id=movie_id, guid=guid)
        if payload.get("ok") is not True:
            duration_ms = int((time.perf_counter() - started) * 1000)
            err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            write_audit_record(
                audit_path=settings.audit_path,
                tool=tool,
                tier=1,
                ok=False,
                duration_ms=duration_ms,
                error_code=str(err.get("code") or "GRAB_FAILED"),
                detail={"movie_id": movie_id, "guid": guid, "title": title, "indexer_id": indexer_id},
            )
            return _worker_error(tool, payload)
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=True,
            duration_ms=duration_ms,
            detail={
                "expected_action": expected,
                "movie_id": movie_id,
                "guid": guid,
                "size_bytes": size_bytes,
                "indexer_id": indexer_id,
                "title": title,
            },
        )
        data = {k: v for k, v in payload.items() if k != "ok"}
        data["mutation"] = True
        data["action"] = "grab"
        data["target"] = f"radarr:movie:{movie_id}:{guid}"
        data["applied"] = payload.get("command") or {"app": "radarr", "movie_id": movie_id, "guid": guid}
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return _arr_upstream_error(tool, "radarr", exc)


def sonarr_release_grab(
    settings: Settings,
    *,
    episode_id: int,
    guid: str,
    size_bytes: int,
    expected_action: str | None = None,
    title: str | None = None,
) -> str:
    started = time.perf_counter()
    tool = TOOL_SONARR_RELEASE_GRAB
    disabled = _check_mutations_enabled(settings, tool)
    if disabled:
        return disabled
    if not settings.sonarr_api_key:
        return error_response(
            tool,
            code="SONARR_NOT_CONFIGURED",
            message="SONARR_API_KEY is not configured",
            hint="Set SONARR_API_KEY in .env and restart homelab-mcp.",
        )
    expected = canonical_grab_sonarr(episode_id, size_bytes)
    gated = _tier1_gate_response(
        settings,
        tool=tool,
        started=started,
        expected=expected,
        expected_action=expected_action,
    )
    if gated:
        return gated
    try:
        with ArrClient(settings.sonarr_url, settings.sonarr_api_key, timeout_seconds=60) as client:
            payload = grab_sonarr_release(client, episode_id=episode_id, guid=guid)
        if payload.get("ok") is not True:
            duration_ms = int((time.perf_counter() - started) * 1000)
            err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            write_audit_record(
                audit_path=settings.audit_path,
                tool=tool,
                tier=1,
                ok=False,
                duration_ms=duration_ms,
                error_code=str(err.get("code") or "GRAB_FAILED"),
                detail={"episode_id": episode_id, "guid": guid, "title": title},
            )
            return _worker_error(tool, payload)
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=True,
            duration_ms=duration_ms,
            detail={
                "expected_action": expected,
                "episode_id": episode_id,
                "guid": guid,
                "size_bytes": size_bytes,
                "title": title,
            },
        )
        data = {k: v for k, v in payload.items() if k != "ok"}
        data["mutation"] = True
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return _arr_upstream_error(tool, "sonarr", exc)


def prowlarr_indexer_grab(
    settings: Settings,
    *,
    indexer_id: int,
    guid: str,
    size_bytes: int,
    expected_action: str | None = None,
    title: str | None = None,
) -> str:
    started = time.perf_counter()
    tool = TOOL_PROWLARR_INDEXER_GRAB
    disabled = _check_mutations_enabled(settings, tool)
    if disabled:
        return disabled
    if not settings.prowlarr_api_key:
        return error_response(
            tool,
            code="PROWLARR_NOT_CONFIGURED",
            message="PROWLARR_API_KEY is not configured",
            hint="Set PROWLARR_API_KEY in .env and restart homelab-mcp.",
        )
    expected = canonical_grab_prowlarr(indexer_id, size_bytes)
    gated = _tier1_gate_response(
        settings,
        tool=tool,
        started=started,
        expected=expected,
        expected_action=expected_action,
    )
    if gated:
        return gated
    try:
        with ProwlarrClient(settings.prowlarr_url, settings.prowlarr_api_key, timeout_seconds=60) as client:
            payload = grab_prowlarr_release(
                client,
                guid=guid,
                indexer_id=indexer_id,
                title=title,
            )
        if payload.get("ok") is not True:
            duration_ms = int((time.perf_counter() - started) * 1000)
            err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            write_audit_record(
                audit_path=settings.audit_path,
                tool=tool,
                tier=1,
                ok=False,
                duration_ms=duration_ms,
                error_code=str(err.get("code") or "GRAB_FAILED"),
                detail={"indexer_id": indexer_id, "guid": guid, "title": title},
            )
            return _worker_error(tool, payload)
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=True,
            duration_ms=duration_ms,
            detail={
                "expected_action": expected,
                "indexer_id": indexer_id,
                "guid": guid,
                "size_bytes": size_bytes,
                "title": title,
            },
        )
        data = {k: v for k, v in payload.items() if k != "ok"}
        data["mutation"] = True
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        if isinstance(exc, httpx.TimeoutException):
            return error_response(
                tool,
                code="UPSTREAM_TIMEOUT",
                message="prowlarr request timed out",
            )
        return error_response(
            tool,
            code="UPSTREAM_ERROR",
            message=f"prowlarr request failed: {exc}",
        )


def sonarr_season_ensure_search(
    settings: Settings,
    *,
    season_number: int,
    series_id: int | None = None,
    query: str | None = None,
    expected_action: str | None = None,
) -> str:
    started = time.perf_counter()
    tool = TOOL_SONARR_SEASON_ENSURE_SEARCH
    disabled = _check_mutations_enabled(settings, tool)
    if disabled:
        return disabled
    if not settings.sonarr_api_key:
        return error_response(
            tool,
            code="SONARR_NOT_CONFIGURED",
            message="SONARR_API_KEY is not configured",
            hint="Set SONARR_API_KEY in .env and restart homelab-mcp.",
        )
    if series_id is None and not (query or "").strip():
        return error_response(
            tool,
            code="INVALID_ARGUMENT",
            message="Pass series_id or query.",
        )

    try:
        with ArrClient(settings.sonarr_url, settings.sonarr_api_key, timeout_seconds=60) as client:
            if series_id is not None:
                expected = canonical_season_ensure(series_id, season_number)
                gated = _tier1_gate_response(
                    settings,
                    tool=tool,
                    started=started,
                    expected=expected,
                    expected_action=expected_action,
                )
                if gated:
                    return gated
                payload = ensure_season_monitored_and_search(
                    client, series_id=series_id, season_number=season_number
                )
            else:
                ctx, ambiguous, err = resolve_series_in_library(
                    client, (query or "").strip(), None
                )
                if err == "AMBIGUOUS_SERIES":
                    return error_response(
                        tool,
                        code="AMBIGUOUS_SERIES",
                        message="Multiple Sonarr series match; pass series_id.",
                        hint=str(ambiguous or ""),
                    )
                if err is not None or ctx is None:
                    return error_response(
                        tool,
                        code=err or "RESOLVE_FAILED",
                        message="Could not resolve series in Sonarr.",
                    )
                expected = canonical_season_ensure(ctx.series_id, season_number)
                gated = _tier1_gate_response(
                    settings,
                    tool=tool,
                    started=started,
                    expected=expected,
                    expected_action=expected_action,
                )
                if gated:
                    return gated
                payload = ensure_season_monitored_and_search(
                    client, series_id=ctx.series_id, season_number=season_number
                )

        if payload.get("ok") is not True:
            duration_ms = int((time.perf_counter() - started) * 1000)
            err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            write_audit_record(
                audit_path=settings.audit_path,
                tool=tool,
                tier=1,
                ok=False,
                duration_ms=duration_ms,
                error_code=str(err.get("code") or "ENSURE_FAILED"),
                detail={"series_id": series_id, "season_number": season_number, "query": query},
            )
            return _worker_error(tool, payload)

        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=True,
            duration_ms=duration_ms,
            detail={
                "expected_action": expected,
                "series_id": payload.get("series", {}).get("id"),
                "season_number": season_number,
            },
        )
        data = {k: v for k, v in payload.items() if k != "ok"}
        data["mutation"] = True
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return _arr_upstream_error(tool, "sonarr", exc)


def _tier2_gate_response(
    settings: Settings,
    *,
    tool: str,
    started: float,
    confirm: bool,
) -> str | None:
    gate_err = require_tier2(confirm=confirm)
    if not gate_err:
        return None
    duration_ms = int((time.perf_counter() - started) * 1000)
    write_audit_record(
        audit_path=settings.audit_path,
        tool=tool,
        tier=2,
        ok=False,
        duration_ms=duration_ms,
        error_code=gate_err["code"],
    )
    return error_response(
        tool,
        code=gate_err["code"],
        message=gate_err["message"],
        hint=gate_err.get("hint", ""),
    )


def _arr_ensure_expected(
    client: ArrClient,
    *,
    media_type: str,
    tmdb_id: int | None,
    tvdb_id: int | None,
    library_id: int | None,
    season: int | None,
) -> str | None:
    mt = media_type.strip().lower()
    if mt == "movie":
        resolved_tmdb = tmdb_id
        if resolved_tmdb is None and library_id is not None:
            movie = client.get(f"/movie/{library_id}")
            if isinstance(movie, dict):
                resolved_tmdb = int_field(movie.get("tmdbId"))
        if resolved_tmdb is None:
            return None
        return canonical_arr_ensure_movie(resolved_tmdb)
    if season is None:
        return None
    stable_id = tvdb_id if tvdb_id is not None else tmdb_id
    if stable_id is None and library_id is not None:
        series = client.get(f"/series/{library_id}")
        if isinstance(series, dict):
            stable_id = int_field(series.get("tvdbId"))
            if stable_id is None:
                stable_id = int_field(series.get("tmdbId"))
    if stable_id is None:
        return None
    return canonical_arr_ensure_series(stable_id, season)


def arr_ensure(
    settings: Settings,
    *,
    media_type: str,
    tmdb_id: int | None = None,
    tvdb_id: int | None = None,
    library_id: int | None = None,
    season: int | None = None,
    expected_action: str | None = None,
) -> str:
    started = time.perf_counter()
    tool = TOOL_ARR_ENSURE
    disabled = _check_mutations_enabled(settings, tool)
    if disabled:
        return disabled
    mt = media_type.strip().lower()
    if mt == "movie":
        if not settings.radarr_api_key:
            return error_response(
                tool,
                code="RADARR_NOT_CONFIGURED",
                message="RADARR_API_KEY is not configured",
                hint="Set RADARR_API_KEY in .env and restart homelab-mcp.",
            )
        base_url, api_key, service = settings.radarr_url, settings.radarr_api_key, "radarr"
    elif mt == "series":
        if not settings.sonarr_api_key:
            return error_response(
                tool,
                code="SONARR_NOT_CONFIGURED",
                message="SONARR_API_KEY is not configured",
                hint="Set SONARR_API_KEY in .env and restart homelab-mcp.",
            )
        base_url, api_key, service = settings.sonarr_url, settings.sonarr_api_key, "sonarr"
    else:
        return error_response(
            tool,
            code="INVALID_ARGUMENT",
            message='media_type must be "movie" or "series"',
        )

    try:
        with ArrClient(base_url, api_key, timeout_seconds=60) as client:
            expected = _arr_ensure_expected(
                client,
                media_type=mt,
                tmdb_id=tmdb_id,
                tvdb_id=tvdb_id,
                library_id=library_id,
                season=season,
            )
            if expected is None:
                return error_response(
                    tool,
                    code="INVALID_ARGUMENT",
                    message="Could not derive expected_action from inputs",
                    hint="Pass tmdb_id (movie) or tvdb_id/tmdb_id + season (series), or library_id.",
                )
            gated = _tier1_gate_response(
                settings,
                tool=tool,
                started=started,
                expected=expected,
                expected_action=expected_action,
            )
            if gated:
                return gated
            payload = worker_arr_ensure(
                client,
                media_type=mt,
                tmdb_id=tmdb_id,
                tvdb_id=tvdb_id,
                library_id=library_id,
                season=season,
            )

        if payload.get("ok") is not True:
            duration_ms = int((time.perf_counter() - started) * 1000)
            err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            write_audit_record(
                audit_path=settings.audit_path,
                tool=tool,
                tier=1,
                ok=False,
                duration_ms=duration_ms,
                error_code=str(err.get("code") or "ENSURE_FAILED"),
                detail={"media_type": mt, "library_id": library_id, "season": season},
            )
            return _worker_error(tool, payload)

        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=True,
            duration_ms=duration_ms,
            detail={
                "expected_action": expected,
                "media_type": mt,
                "library_id": payload.get("library_id"),
                "season": season,
            },
        )
        data = {k: v for k, v in payload.items() if k != "ok"}
        data["mutation"] = True
        data["already_existed"] = not bool(data.get("created"))
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return _arr_upstream_error(tool, service, exc)


def arr_queue_remove(
    settings: Settings,
    *,
    media_type: str,
    queue_id: int,
    remove_from_client: bool = True,
    blocklist: bool = False,
    confirm: bool = False,
) -> str:
    started = time.perf_counter()
    tool = TOOL_ARR_QUEUE_REMOVE
    disabled = _check_mutations_enabled(settings, tool)
    if disabled:
        return disabled
    gated = _tier2_gate_response(settings, tool=tool, started=started, confirm=confirm)
    if gated:
        return gated

    if queue_id <= 0:
        return error_response(
            tool,
            code="INVALID_ARGUMENT",
            message="queue_id must be a positive integer",
        )

    mt = media_type.strip().lower()
    if mt == "movie":
        if not settings.radarr_api_key:
            return error_response(
                tool,
                code="RADARR_NOT_CONFIGURED",
                message="RADARR_API_KEY is not configured",
                hint="Set RADARR_API_KEY in .env and restart homelab-mcp.",
            )
        base_url, api_key, service = settings.radarr_url, settings.radarr_api_key, "radarr"
    elif mt == "series":
        if not settings.sonarr_api_key:
            return error_response(
                tool,
                code="SONARR_NOT_CONFIGURED",
                message="SONARR_API_KEY is not configured",
                hint="Set SONARR_API_KEY in .env and restart homelab-mcp.",
            )
        base_url, api_key, service = settings.sonarr_url, settings.sonarr_api_key, "sonarr"
    else:
        return error_response(
            tool,
            code="INVALID_ARGUMENT",
            message='media_type must be "movie" or "series"',
        )

    try:
        with ArrClient(base_url, api_key, timeout_seconds=60) as client:
            payload = worker_arr_queue_remove(
                client,
                queue_id=queue_id,
                remove_from_client=remove_from_client,
                blocklist=blocklist,
            )
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=2,
            ok=True,
            duration_ms=duration_ms,
            detail={
                "queue_id": queue_id,
                "remove_from_client": remove_from_client,
                "blocklist": blocklist,
            },
        )
        data = {k: v for k, v in payload.items() if k != "ok"}
        data["mutation"] = True
        data["action"] = "remove"
        data["target"] = f"queue:{queue_id}"
        return ok_response(tool, data, duration_ms=duration_ms)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=2,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return _arr_upstream_error(tool, service, exc)
