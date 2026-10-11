from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.config import get_config_bool, get_config_int, load_worker_yaml_config
from homelab_workers.shared.prowlarr import ProwlarrClient
from homelab_workers.shared.prowlarr_releases import grab_prowlarr_release
from homelab_workers.shared.qbittorrent import QBittorrentClient
from homelab_workers.shared.radarr_releases import ReleaseSearchConfig, search_radarr_releases
from homelab_workers.shared.seerr import SeerrClient, normalize_seerr_request
from homelab_workers.shared.seerr_reconciler import diagnose_request
from homelab_workers.shared.seerr_reconciler_state import (
    build_reconciler_status,
    evaluate_reconciler_health,
    record_attempt,
    read_reconciler_state,
    write_reconciler_state,
)
from homelab_workers.shared.secrets import bootstrap_container_secrets
from homelab_workers.shared.sonarr_releases import (
    SonarrReleaseSearchConfig,
    search_sonarr_releases,
)


@dataclass(frozen=True)
class ReconcilerConfig:
    enabled: bool
    fallback_enabled: bool
    lookback_hours: int
    interval_seconds: int
    grace_seconds: int
    search_cooldown_seconds: int
    status_path: str
    state_path: str
    status_max_age_seconds: int


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--healthcheck", action="store_true")
    return parser.parse_args(argv)


def _yaml() -> dict[str, object]:
    data = load_worker_yaml_config().get("seerr_reconciler", {})
    return data if isinstance(data, dict) else {}


def load_reconciler_config() -> ReconcilerConfig:
    yaml_config = _yaml()
    env = os.environ
    return ReconcilerConfig(
        enabled=get_config_bool(env, yaml_config, "SEERR_RECONCILER_ENABLED", "enabled", False),
        fallback_enabled=get_config_bool(
            env,
            yaml_config,
            "SEERR_RECONCILER_FALLBACK_ENABLED",
            "fallback_enabled",
            False,
        ),
        lookback_hours=get_config_int(
            env, yaml_config, "SEERR_RECONCILER_LOOKBACK_HOURS", "lookback_hours", 24
        ),
        interval_seconds=get_config_int(
            env, yaml_config, "SEERR_RECONCILER_INTERVAL_SECONDS", "interval_seconds", 300
        ),
        grace_seconds=get_config_int(
            env, yaml_config, "SEERR_RECONCILER_GRACE_SECONDS", "grace_seconds", 900
        ),
        search_cooldown_seconds=get_config_int(
            env,
            yaml_config,
            "SEERR_RECONCILER_SEARCH_COOLDOWN_SECONDS",
            "search_cooldown_seconds",
            3600,
        ),
        status_path=os.environ.get("SEERR_RECONCILER_STATUS_PATH", "/state/status.json"),
        state_path=os.environ.get("SEERR_RECONCILER_STATE_PATH", "/state/state.json"),
        status_max_age_seconds=get_config_int(
            env,
            yaml_config,
            "SEERR_RECONCILER_STATUS_MAX_AGE_SECONDS",
            "status_max_age_seconds",
            900,
        ),
    )


def _arr_client_for(config: ReconcilerConfig, media_type: str) -> ArrClient:
    del config
    if media_type == "movie":
        return ArrClient(
            os.environ.get("RADARR_INTERNAL_URL", "http://radarr:7878/radarr"),
            os.environ.get("RADARR_API_KEY", ""),
        )
    return ArrClient(
        os.environ.get("SONARR_INTERNAL_URL", "http://sonarr:8989/sonarr"),
        os.environ.get("SONARR_API_KEY", ""),
    )


def _arr_library_status(
    client: ArrClient,
    *,
    media_type: str,
    arr_id: int | None,
) -> dict[str, object]:
    if arr_id is None:
        return {"in_library": False, "monitored": False}
    path = f"/movie/{arr_id}" if media_type == "movie" else f"/series/{arr_id}"
    try:
        row = client.get(path)
    except Exception:
        collection_path = "/movie" if media_type == "movie" else "/series"
        collection = client.get(collection_path)
        row = next(
            (
                item
                for item in collection
                if isinstance(item, dict) and int(item.get("id") or 0) == arr_id
            ),
            None,
        )
    if not isinstance(row, dict):
        return {"in_library": False, "monitored": False}
    return {
        "in_library": True,
        "monitored": bool(row.get("monitored")),
        "has_file": bool(row.get("hasFile")) if media_type == "movie" else False,
        "arr_id": arr_id,
        "title": row.get("title"),
        "tmdb_id": row.get("tmdbId"),
        "tvdb_id": row.get("tvdbId"),
        "imdb_id": row.get("imdbId"),
    }


def _prowlarr_releases(prowlarr: ProwlarrClient, request: Any) -> list[dict[str, object]]:
    query = request.title
    if request.media_type == "series" and request.seasons:
        query = f"{request.title} S{request.seasons[0]:02d}"
    rows = prowlarr.search(
        query=query,
        limit=25,
        tvdb_id=request.stable_ids.tvdb_id,
        tmdb_id=request.stable_ids.tmdb_id,
        imdb_id=request.stable_ids.imdb_id,
    )
    out: list[dict[str, object]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "title": item.get("title"),
                "guid": item.get("guid"),
                "indexer_id": item.get("indexerId") or item.get("indexer_id"),
                "size_bytes": item.get("size") or item.get("size_bytes"),
                "seeders": item.get("seeders"),
                "leechers": item.get("leechers"),
                "rejections": item.get("rejections") or [],
            }
        )
    return out


def _release_search_config(config: ReconcilerConfig) -> ReleaseSearchConfig:
    timeout = max(1.0, min(float(config.search_cooldown_seconds), 30.0))
    return ReleaseSearchConfig(search_timeout_s=timeout, poll_s=2.0, options_limit=25)


def _sonarr_release_search_config(config: ReconcilerConfig) -> SonarrReleaseSearchConfig:
    base = _release_search_config(config)
    return SonarrReleaseSearchConfig(
        search_timeout_s=base.search_timeout_s,
        poll_s=base.poll_s,
        options_limit=base.options_limit,
        max_episode_release_lookups=15,
    )


def _arr_releases_for_request(
    client: ArrClient,
    request: Any,
    config: ReconcilerConfig,
) -> list[dict[str, object]]:
    try:
        if request.media_type == "movie":
            result = search_radarr_releases(
                client,
                query=request.title,
                movie_id=request.arr_id,
                config=_release_search_config(config),
            )
        else:
            season = request.seasons[0] if request.seasons else 1
            result = search_sonarr_releases(
                client,
                query=request.title,
                season=season,
                series_id=request.arr_id,
                include_full_series_packs=True,
                config=_sonarr_release_search_config(config),
            )
    except Exception:
        return []
    options = result.get("options") if isinstance(result, dict) and result.get("ok") else None
    if not isinstance(options, list):
        return []
    return [option for option in options if isinstance(option, dict)]


def _request_state_row(state: dict[str, object], request_key: str) -> dict[str, object]:
    requests = state.get("requests")
    if not isinstance(requests, dict):
        return {}
    row = requests.get(request_key)
    return row if isinstance(row, dict) else {}


def _stored_arr_releases(row: dict[str, object]) -> list[dict[str, object]]:
    evidence = row.get("evidence")
    if not isinstance(evidence, dict):
        return []
    arr_rejections = evidence.get("arr_rejections")
    if not isinstance(arr_rejections, list):
        return []
    return [item for item in arr_rejections if isinstance(item, dict)]


def _recent_timestamp(value: object, *, now: int, cooldown_seconds: int) -> bool:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return False
    return timestamp > 0 and now - timestamp < max(cooldown_seconds, 0)


def _recent_attempt_for_guid(
    row: dict[str, object],
    guid: str,
    *,
    now: int,
    cooldown_seconds: int,
) -> bool:
    attempts = row.get("attempts")
    if not isinstance(attempts, list):
        return False
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        if str(attempt.get("guid") or "") != guid:
            continue
        if _recent_timestamp(attempt.get("at"), now=now, cooldown_seconds=cooldown_seconds):
            return True
    return False


def _selected_fallback_candidate(row: dict[str, object]) -> dict[str, object] | None:
    expected = str(row.get("expected_action") or "")
    prefix = "grab:prowlarr:"
    if not expected.startswith(prefix):
        return None
    parts = expected.split(":", 3)
    if len(parts) != 4:
        return None
    try:
        indexer_id = int(parts[2])
    except ValueError:
        return None
    guid = parts[3]
    candidates = row.get("candidates")
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("guid") or "") == guid and int(candidate.get("indexer_id") or 0) == indexer_id:
            return candidate
    return None


def _apply_fallback_grabs(
    config: ReconcilerConfig,
    state: dict[str, object],
    rows: list[dict[str, object]],
    prowlarr: ProwlarrClient,
    *,
    dry_run: bool,
) -> int:
    if dry_run or not config.fallback_enabled:
        return 0
    mutated = 0
    now = int(time.time())
    for row in rows:
        if row.get("recommended_action") != "grab_fallback":
            continue
        key = str(row.get("state_key") or "")
        candidate = _selected_fallback_candidate(row)
        if not key or candidate is None:
            continue
        guid = str(candidate.get("guid") or "")
        existing_row = _request_state_row(state, key)
        if _recent_attempt_for_guid(
            existing_row,
            guid,
            now=now,
            cooldown_seconds=config.search_cooldown_seconds,
        ):
            continue
        indexer_id = int(candidate.get("indexer_id") or 0)
        title = str(candidate.get("title") or "")
        result = grab_prowlarr_release(
            prowlarr,
            guid=guid,
            indexer_id=indexer_id,
            title=title or None,
        )
        ok = bool(result.get("ok")) if isinstance(result, dict) else False
        record_attempt(state, key, guid, ok=ok, now=now)
        row["fallback_grab_result"] = result
        if ok:
            row["state"] = "fallback_grabbed"
            row["reason"] = "Prowlarr fallback grab accepted"
            row["grabbed_at"] = now
            mutated += 1
        else:
            error = result.get("error") if isinstance(result, dict) else None
            message = (
                str(error.get("message"))
                if isinstance(error, dict) and error.get("message")
                else "Prowlarr fallback grab failed"
            )
            row["state"] = "blocked_no_candidate"
            row["reason"] = message
    return mutated


def _diagnosis_to_state_row(result: Any) -> dict[str, object]:
    return {
        "state_key": result.request.state_key,
        "request_id": result.request.request_id,
        "diagnosed_at": int(time.time()),
        "title": result.request.title,
        "media_type": result.request.media_type,
        "seasons": list(result.request.seasons),
        "stable_ids": result.request.stable_ids.as_dict(),
        "arr_id": result.request.arr_id,
        "state": result.state,
        "reason": result.reason,
        "evidence": result.evidence,
        "recommended_action": result.recommended_action,
        "expected_action": result.expected_action,
        "candidates": [
            {
                "title": candidate.title,
                "guid": candidate.guid,
                "indexer_id": candidate.indexer_id,
                "size_bytes": candidate.size_bytes,
                "seeders": candidate.seeders,
                "rejections": list(candidate.rejections),
                "download_allowed": candidate.download_allowed,
                "search_mode": candidate.search_mode,
            }
            for candidate in result.candidates
        ],
    }


def _requested_at_timestamp(requested_at: str | None) -> float | None:
    if not requested_at:
        return None
    try:
        parsed = datetime.fromisoformat(requested_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def fetch_and_diagnose_requests(
    config: ReconcilerConfig,
    *,
    dry_run: bool,
) -> list[dict[str, object]]:
    state = read_reconciler_state(config.state_path)
    lookback_cutoff = time.time() - max(config.lookback_hours, 0) * 3600
    with (
        SeerrClient(
            os.environ.get("OVERSEERR_INTERNAL_URL", "http://overseerr:5055"),
            os.environ.get("OVERSEERR_API_KEY", ""),
        ) as seerr,
        ProwlarrClient(
            os.environ.get("PROWLARR_INTERNAL_URL", "http://prowlarr:9696/prowlarr"),
            os.environ.get("PROWLARR_API_KEY", ""),
        ) as prowlarr,
        QBittorrentClient(
            os.environ.get("QBITTORRENT_INTERNAL_URL", "http://qbittorrent:8080"),
            os.environ.get("QBITTORRENT_USERNAME", ""),
            os.environ.get("QBITTORRENT_PASSWORD", ""),
        ) as qbt,
    ):
        qbt_torrents = qbt.torrents_info_raw()
        rows: list[dict[str, object]] = []
        for raw in seerr.requests(take=100, filter_status="approved"):
            request = normalize_seerr_request(raw)
            if request is None:
                continue
            requested_at = _requested_at_timestamp(request.requested_at)
            if requested_at is None or requested_at < lookback_cutoff:
                continue
            with _arr_client_for(config, request.media_type) as arr:
                arr_status = _arr_library_status(
                    arr,
                    media_type=request.media_type,
                    arr_id=request.arr_id,
                )
                existing_row = _request_state_row(state, request.state_key)
                now = int(time.time())
                if dry_run or _recent_timestamp(
                    existing_row.get("last_arr_search_at"),
                    now=now,
                    cooldown_seconds=config.search_cooldown_seconds,
                ):
                    arr_releases = _stored_arr_releases(existing_row)
                    arr_search_at = None
                else:
                    arr_releases = _arr_releases_for_request(arr, request, config)
                    arr_search_at = now
            result = diagnose_request(
                request,
                arr_status=arr_status,
                arr_releases=arr_releases,
                prowlarr_releases=_prowlarr_releases(prowlarr, request),
                qbt_torrents=qbt_torrents,
                state=state,
            )
            row = _diagnosis_to_state_row(result)
            if arr_search_at is not None:
                row["last_arr_search_at"] = arr_search_at
            rows.append(row)
        return rows


def _merge_diagnoses(
    state: dict[str, object], diagnoses: list[dict[str, object]]
) -> dict[str, object]:
    requests = state.setdefault("requests", {})
    if not isinstance(requests, dict):
        state["requests"] = requests = {}
    for row in diagnoses:
        key = str(row.get("state_key") or f"{row.get('request_id')}:unknown")
        existing = requests.get(key)
        requests[key] = {**existing, **row} if isinstance(existing, dict) else row
    return state


def run_once(config: ReconcilerConfig, *, dry_run: bool) -> int:
    started = int(time.time())
    state = read_reconciler_state(config.state_path)
    diagnoses = (
        fetch_and_diagnose_requests(config, dry_run=dry_run) if config.enabled or dry_run else []
    )
    mutated = 0
    if diagnoses and config.fallback_enabled and not dry_run:
        with ProwlarrClient(
            os.environ.get("PROWLARR_INTERNAL_URL", "http://prowlarr:9696/prowlarr"),
            os.environ.get("PROWLARR_API_KEY", ""),
        ) as prowlarr:
            mutated = _apply_fallback_grabs(
                config,
                state,
                diagnoses,
                prowlarr,
                dry_run=dry_run,
            )
    state = _merge_diagnoses(state, diagnoses)
    write_reconciler_state(config.state_path, state)
    status = build_reconciler_status(
        enabled=config.enabled,
        fallback_enabled=config.fallback_enabled,
        running=False,
        ok=True,
        dry_run=dry_run,
        started_at=started,
        completed_at=int(time.time()),
        diagnosed=len(diagnoses),
        mutated=mutated,
        blocked=sum(1 for row in diagnoses if str(row.get("state", "")).startswith("blocked_")),
    )
    write_reconciler_state(config.status_path, status)
    return 0


def healthcheck(config: ReconcilerConfig) -> int:
    status = read_reconciler_state(config.status_path)
    health = evaluate_reconciler_health(status, config.status_max_age_seconds)
    return 0 if health["healthy"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    bootstrap_container_secrets()
    args = parse_args(argv)
    config = load_reconciler_config()
    if args.healthcheck:
        return healthcheck(config)
    if args.once:
        return run_once(config, dry_run=bool(args.dry_run))
    while True:
        run_once(config, dry_run=False)
        time.sleep(max(config.interval_seconds, 1))
