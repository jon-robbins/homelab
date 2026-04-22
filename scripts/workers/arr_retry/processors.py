from __future__ import annotations

import argparse
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass

from .client import ArrClient, read_api_key_from_config_xml
from .health import (
    HealthCandidate,
    HealthDecision,
    HealthDecisionPlan,
    HealthPolicySettings,
    HealthStateStore,
    apply_action_budget,
    evaluate_candidate,
    now_unix_ts,
)
from .logic import RetryPlan, analyze_releases, choose_force_grab_candidate, queue_item_looks_stalled
from .qbittorrent import QBittorrentClient, QBTorrent

log = logging.getLogger("arr_retry")
_QBT_ORPHAN_STALL_STATES = frozenset({"stalledDL", "missingFiles"})


def _env_flag(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _plan_sonarr_qbt_stalled_orphans(
    args: argparse.Namespace,
    sonarr_client: ArrClient,
    queue_episode_ids: set[int],
    plan: RetryPlan,
    orphan_hashes_out: list[str],
) -> None:
    """
    Sonarr queue can be empty while qBittorrent still holds dead torrents in the Sonarr category.
    Parse each stalled torrent name -> library episode; if still monitored+missing and not in queue,
    schedule EpisodeSearch and (in apply mode) remove the stale torrent from qBittorrent.
    """
    if not _env_flag("ARR_RETRY_QBT_ORPHAN_STALLS", "true"):
        return
    if not args.qbittorrent_username or not args.qbittorrent_password:
        return
    max_ops = max(0, int(os.environ.get("ARR_RETRY_MAX_QB_ORPHANS", "10")))
    if max_ops == 0:
        return
    min_age = max(0, int(os.environ.get("ARR_RETRY_QBT_ORPHAN_MIN_AGE_SECONDS", "3600")))
    category_want = (os.environ.get("SONARR_QBT_CATEGORY", "tv-sonarr").strip() or "tv-sonarr").casefold()

    try:
        qb = QBittorrentClient(
            base_url=args.qbittorrent_url,
            username=args.qbittorrent_username,
            password=args.qbittorrent_password,
            timeout_seconds=args.http_timeout_seconds,
        )
        qb.login()
        torrents = qb.torrents_info()
    except Exception as exc:  # noqa: BLE001
        log.warning("[sonarr][qb-orphan][warn] qBittorrent: %s", exc)
        return

    now = int(time.time())
    planned = 0
    seen_hash: set[str] = set()

    for tor in torrents:
        if planned >= max_ops:
            break
        if not tor.name or tor.is_complete:
            continue
        if (tor.category or "").casefold() != category_want:
            continue
        if tor.state not in _QBT_ORPHAN_STALL_STATES:
            continue
        if tor.progress >= 0.98:
            continue
        if min_age and (now - tor.added_on) < min_age:
            continue

        try:
            parsed = sonarr_client.get("/parse", {"title": tor.name})
        except Exception as exc:  # noqa: BLE001
            log.warning("[sonarr][qb-orphan][warn] parse failed name=%r: %s", tor.name, exc)
            continue
        if not isinstance(parsed, dict):
            continue
        episodes = parsed.get("episodes") or []
        touched_episodes: list[int] = []
        for ep in episodes:
            if not isinstance(ep, dict):
                continue
            if not ep.get("monitored", True):
                continue
            if ep.get("hasFile") or ep.get("episodeFileId"):
                continue
            eid = ep.get("id")
            if not isinstance(eid, int):
                continue
            if eid in queue_episode_ids:
                continue
            if planned >= max_ops:
                break
            log.info(
                "[sonarr][qb-orphan-stall] hash=%s state=%s episodeId=%s name=%r -> EpisodeSearch (+ qB remove in apply)",
                tor.torrent_hash,
                tor.state,
                eid,
                tor.name,
            )
            plan.search_ids.add(eid)
            touched_episodes.append(eid)
            planned += 1
        if touched_episodes and tor.torrent_hash not in seen_hash:
            seen_hash.add(tor.torrent_hash)
            orphan_hashes_out.append(tor.torrent_hash)


def _delete_qbt_orphan_hashes(args: argparse.Namespace, hashes: list[str], dry_run: bool) -> None:
    uniq = list(dict.fromkeys(hashes))
    if not uniq:
        return
    if dry_run:
        log.info("[sonarr][qb-orphan][dry-run] would remove %s torrent(s) from qBittorrent", len(uniq))
        return
    try:
        qb = QBittorrentClient(
            base_url=args.qbittorrent_url,
            username=args.qbittorrent_username,
            password=args.qbittorrent_password,
            timeout_seconds=args.http_timeout_seconds,
        )
        qb.torrents_delete_hashes(uniq, delete_files=False)
        log.info("[sonarr][qb-orphan][ok] removed %s stale torrent(s) from qBittorrent", len(uniq))
    except Exception as exc:  # noqa: BLE001
        log.warning("[sonarr][qb-orphan][warn] qBittorrent delete failed: %s", exc)


@dataclass
class AppProcessResult:
    retries_planned: int
    health_actions_used: int


def process_sonarr(
    args: argparse.Namespace,
    dry_run: bool,
    health_actions_remaining: int,
    state_store: HealthStateStore,
) -> AppProcessResult:
    key = args.sonarr_api_key or read_api_key_from_config_xml(args.sonarr_config_path)
    if not key:
        log.warning("[sonarr] No API key found; skipping.")
        return AppProcessResult(retries_planned=0, health_actions_used=0)

    client = ArrClient(args.sonarr_url, key, args.http_timeout_seconds)
    queue = _load_queue(client, "sonarr")
    stalled = [item for item in queue if queue_item_looks_stalled(item)]
    queue_episode_ids = {item.get("episodeId") for item in queue if isinstance(item.get("episodeId"), int)}
    log.info("[sonarr] queue=%s stalled=%s", len(queue), len(stalled))

    plan = RetryPlan()
    if args.search_missing_monitored:
        _plan_sonarr_missing(args, client, queue_episode_ids, plan)
    _add_stalled_sonarr_items(stalled, plan)

    health_plan = _plan_health_actions(
        args=args,
        app_label="sonarr",
        item_label="episodeId",
        queue=queue,
        client=client,
        state_store=state_store,
        health_actions_remaining=health_actions_remaining,
    )
    for candidate in health_plan.replace_candidates + health_plan.race_candidates:
        plan.search_ids.add(candidate.item_id)

    orphan_hashes: list[str] = []
    _plan_sonarr_qbt_stalled_orphans(args, client, queue_episode_ids, plan, orphan_hashes)

    plan.cap(args.max_searches)
    log.info("[sonarr] retries planned=%s", len(plan.search_ids))
    if args.allow_force_grab_fallback:
        log.info("[sonarr] force-grab planned=%s", len(plan.force_grab_candidates))
    if _health_enabled(args):
        log.info(
            "[sonarr] health actions planned=%s replace=%s race=%s",
            health_plan.action_count,
            len(health_plan.replace_candidates),
            len(health_plan.race_candidates),
        )

    if dry_run:
        _delete_qbt_orphan_hashes(args, orphan_hashes, dry_run=True)
        return AppProcessResult(retries_planned=plan.total, health_actions_used=health_plan.action_count)

    _remove_stalled_queue_items(client, stalled, "sonarr")
    _remove_health_replace_queue_items(client, health_plan.replace_candidates, "sonarr")
    _delete_qbt_orphan_hashes(args, orphan_hashes, dry_run=False)
    _run_searches(client, sorted(plan.search_ids), "sonarr", "EpisodeSearch", "episodeId", "episodeIds")
    _run_force_grabs(client, sorted(plan.force_grab_candidates.items()), "sonarr", "episodeId")
    _record_health_actions(state_store, "sonarr", health_plan, now_unix_ts())
    return AppProcessResult(retries_planned=plan.total, health_actions_used=health_plan.action_count)


def process_radarr(
    args: argparse.Namespace,
    dry_run: bool,
    health_actions_remaining: int,
    state_store: HealthStateStore,
) -> AppProcessResult:
    if args.no_radarr:
        log.info("[radarr] Skipped by --no-radarr")
        return AppProcessResult(retries_planned=0, health_actions_used=0)

    key = args.radarr_api_key or read_api_key_from_config_xml(args.radarr_config_path)
    if not key:
        log.warning("[radarr] No API key found; skipping.")
        return AppProcessResult(retries_planned=0, health_actions_used=0)

    client = ArrClient(args.radarr_url, key, args.http_timeout_seconds)
    queue = _load_queue(client, "radarr")
    stalled = [item for item in queue if queue_item_looks_stalled(item)]
    queue_movie_ids = {item.get("movieId") for item in queue if isinstance(item.get("movieId"), int)}
    log.info("[radarr] queue=%s stalled=%s", len(queue), len(stalled))

    plan = RetryPlan()
    if args.search_missing_monitored:
        _plan_radarr_missing(args, client, queue_movie_ids, plan)
    _add_stalled_radarr_items(stalled, plan)

    health_plan = _plan_health_actions(
        args=args,
        app_label="radarr",
        item_label="movieId",
        queue=queue,
        client=client,
        state_store=state_store,
        health_actions_remaining=health_actions_remaining,
    )
    for candidate in health_plan.replace_candidates + health_plan.race_candidates:
        plan.search_ids.add(candidate.item_id)

    plan.cap(args.max_searches)
    log.info("[radarr] retries planned=%s", len(plan.search_ids))
    if args.allow_force_grab_fallback:
        log.info("[radarr] force-grab planned=%s", len(plan.force_grab_candidates))
    if _health_enabled(args):
        log.info(
            "[radarr] health actions planned=%s replace=%s race=%s",
            health_plan.action_count,
            len(health_plan.replace_candidates),
            len(health_plan.race_candidates),
        )

    if dry_run:
        return AppProcessResult(retries_planned=plan.total, health_actions_used=health_plan.action_count)

    _remove_stalled_queue_items(client, stalled, "radarr")
    _remove_health_replace_queue_items(client, health_plan.replace_candidates, "radarr")
    _run_searches(client, sorted(plan.search_ids), "radarr", "MoviesSearch", "movieId", "movieIds")
    _run_force_grabs(client, sorted(plan.force_grab_candidates.items()), "radarr", "movieId")
    _record_health_actions(state_store, "radarr", health_plan, now_unix_ts())
    return AppProcessResult(retries_planned=plan.total, health_actions_used=health_plan.action_count)


def _load_queue(client: ArrClient, app_label: str) -> list[dict]:
    queue = client.get("/queue/details", {"all": "true"})
    if not isinstance(queue, list):
        raise RuntimeError(f"Unexpected {app_label.capitalize()} queue payload")
    return queue


def _plan_sonarr_missing(
    args: argparse.Namespace,
    client: ArrClient,
    queue_episode_ids: set[int],
    plan: RetryPlan,
) -> None:
    allowed_series = set(args.series_id) if args.series_id else None
    series = client.get("/series")
    if not isinstance(series, list):
        return

    checked = 0
    for entry in series:
        series_id = entry.get("id")
        if not isinstance(series_id, int):
            continue
        if allowed_series is not None and series_id not in allowed_series:
            continue

        episodes = client.get("/episode", {"seriesId": series_id})
        if not isinstance(episodes, list):
            continue

        for episode in episodes:
            if checked >= args.max_missing_checks:
                return
            episode_id = episode.get("id")
            if not isinstance(episode_id, int):
                continue
            if not episode.get("monitored") or episode.get("hasFile"):
                continue
            if episode_id in queue_episode_ids:
                continue

            checked += 1
            releases = _safe_release_lookup(client, {"episodeId": episode_id}, "sonarr", "episodeId", episode_id)
            if not releases:
                if _env_flag("ARR_RETRY_MISSING_EMPTY_RELEASE_SEARCH", "true"):
                    sn = episode.get("seasonNumber")
                    en = episode.get("episodeNumber")
                    ep_title = str(episode.get("title") or "")
                    series_title = str(entry.get("title") or "")
                    if isinstance(sn, int) and isinstance(en, int):
                        label = f"{series_title} S{sn:02d}E{en:02d} - {ep_title}".strip(" -")
                    else:
                        label = f"{series_title} - {ep_title}".strip(" -")
                    log.info(
                        "[sonarr][missing-empty-release-cache] episodeId=%s episode=%r -> EpisodeSearch",
                        episode_id,
                        label,
                    )
                    plan.search_ids.add(episode_id)
                continue
            _update_plan_from_releases(
                args=args,
                app_label="sonarr",
                item_label="episodeId",
                item_id=episode_id,
                title=str(entry.get("title")),
                releases=releases,
                rejected_suffix="seeded releases exist but all are rejected by filters/rules",
                plan=plan,
            )


def _plan_radarr_missing(
    args: argparse.Namespace,
    client: ArrClient,
    queue_movie_ids: set[int],
    plan: RetryPlan,
) -> None:
    allowed_movies = set(args.movie_id) if args.movie_id else None
    movies = client.get("/movie")
    if not isinstance(movies, list):
        return

    checked = 0
    for movie in movies:
        if checked >= args.max_missing_checks:
            return
        movie_id = movie.get("id")
        if not isinstance(movie_id, int):
            continue
        if allowed_movies is not None and movie_id not in allowed_movies:
            continue
        if not movie.get("monitored") or movie.get("hasFile"):
            continue
        if movie_id in queue_movie_ids:
            continue

        checked += 1
        releases = _safe_release_lookup(client, {"movieId": movie_id}, "radarr", "movieId", movie_id)
        if not releases:
            if _env_flag("ARR_RETRY_MISSING_EMPTY_RELEASE_SEARCH", "true"):
                log.info(
                    "[radarr][missing-empty-release-cache] movieId=%s title=%r -> MoviesSearch",
                    movie_id,
                    str(movie.get("title") or ""),
                )
                plan.search_ids.add(movie_id)
            continue
        _update_plan_from_releases(
            args=args,
            app_label="radarr",
            item_label="movieId",
            item_id=movie_id,
            title=str(movie.get("title")),
            releases=releases,
            rejected_suffix="seeded releases exist but all are rejected",
            plan=plan,
        )


def _safe_release_lookup(
    client: ArrClient,
    query: dict[str, int],
    app_label: str,
    item_label: str,
    item_id: int,
) -> list[dict]:
    try:
        releases = client.get("/release", query)
    except Exception as exc:  # noqa: BLE001
        log.warning("[%s][warn] release lookup failed %s=%s: %s", app_label, item_label, item_id, exc)
        return []
    return releases if isinstance(releases, list) else []


def _update_plan_from_releases(
    args: argparse.Namespace,
    app_label: str,
    item_label: str,
    item_id: int,
    title: str,
    releases: list[dict],
    rejected_suffix: str,
    plan: RetryPlan,
) -> None:
    has_approved, has_seeded = analyze_releases(releases, args.min_seeders)
    if has_approved:
        log.info(
            "[%s][stuck+available] %s=%s title=%r has approved release(s) but no active queue item",
            app_label,
            item_label,
            item_id,
            title,
        )
        plan.search_ids.add(item_id)
        return

    if not has_seeded:
        return

    log.info("[%s][available-but-filtered] %s=%s %s", app_label, item_label, item_id, rejected_suffix)
    if not args.allow_force_grab_fallback:
        return

    candidate = choose_force_grab_candidate(releases, args.min_seeders)
    if candidate is None:
        return

    plan.force_grab_candidates[item_id] = candidate
    log.info(
        "[%s][force-grab-candidate] %s=%s seeders=%s title=%r",
        app_label,
        item_label,
        item_id,
        candidate.get("seeders"),
        candidate.get("title"),
    )


def _add_stalled_sonarr_items(stalled: list[dict], plan: RetryPlan) -> None:
    for item in stalled:
        episode_id = item.get("episodeId")
        if isinstance(episode_id, int):
            plan.search_ids.add(episode_id)
        log.info(
            "[sonarr][stalled] queueId=%s episodeId=%s title=%r",
            item.get("id"),
            item.get("episodeId"),
            item.get("title"),
        )


def _add_stalled_radarr_items(stalled: list[dict], plan: RetryPlan) -> None:
    for item in stalled:
        movie_id = item.get("movieId")
        if isinstance(movie_id, int):
            plan.search_ids.add(movie_id)
        log.info(
            "[radarr][stalled] queueId=%s movieId=%s title=%r",
            item.get("id"),
            item.get("movieId"),
            item.get("title"),
        )


def _remove_stalled_queue_items(client: ArrClient, stalled: list[dict], app_label: str) -> None:
    for item in stalled:
        queue_id = item.get("id")
        if queue_id is None:
            continue
        try:
            client.delete(
                f"/queue/{queue_id}",
                {
                    "removeFromClient": "true",
                    "blocklist": "true",
                    "skipRedownload": "false",
                },
            )
            log.info("[%s][ok] removed stalled queue item %s", app_label, queue_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s][warn] remove failed for queue %s: %s", app_label, queue_id, exc)


def _run_searches(
    client: ArrClient,
    ids: list[int],
    app_label: str,
    command_name: str,
    log_key: str,
    payload_key: str,
) -> None:
    for item_id in ids:
        try:
            response = client.post("/command", {"name": command_name, payload_key: [item_id]})
            command_id = response.get("id") if isinstance(response, dict) else None
            log.info("[%s][ok] %s %s=%s commandId=%s", app_label, command_name, log_key, item_id, command_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s][warn] %s failed for %s=%s: %s", app_label, command_name, log_key, item_id, exc)


def _run_force_grabs(
    client: ArrClient,
    items: Iterable[tuple[int, dict]],
    app_label: str,
    item_label: str,
) -> None:
    for item_id, release in items:
        try:
            response = client.post("/release", release)
            release_id = response.get("id") if isinstance(response, dict) else None
            log.info(
                "[%s][ok] force-grabbed %s=%s title=%r releaseId=%s",
                app_label,
                item_label,
                item_id,
                release.get("title"),
                release_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s][warn] force-grab failed for %s=%s: %s", app_label, item_label, item_id, exc)


def _health_enabled(args: argparse.Namespace) -> bool:
    return args.enable_health_replacement or args.enable_health_race


def _health_policy_from_args(args: argparse.Namespace) -> HealthPolicySettings:
    return HealthPolicySettings(
        replace_age_seconds=int(args.health_replace_age_hours * 3600),
        race_age_seconds=int(args.health_race_age_hours * 3600),
        min_average_speed_bps=float(args.health_min_avg_speed_kib) * 1024.0,
        min_progress_to_skip_replace=float(args.health_skip_progress_percent) / 100.0,
        cooldown_seconds=int(args.health_cooldown_hours * 3600),
        max_replacements_per_day=args.health_max_replacements_per_day,
        max_actions_per_sweep=args.health_max_actions_per_sweep,
        enable_health_replacement=args.enable_health_replacement,
        enable_health_race=args.enable_health_race,
    )


def _plan_health_actions(
    args: argparse.Namespace,
    app_label: str,
    item_label: str,
    queue: list[dict],
    client: ArrClient,
    state_store: HealthStateStore,
    health_actions_remaining: int,
) -> HealthDecisionPlan:
    if not _health_enabled(args):
        return HealthDecisionPlan()
    if health_actions_remaining <= 0:
        log.info("[%s] health budget exhausted before processing.", app_label)
        return HealthDecisionPlan()
    if not args.qbittorrent_username or not args.qbittorrent_password:
        log.warning("[%s][warn] qBittorrent credentials missing; skipping health checks.", app_label)
        return HealthDecisionPlan()

    torrents_by_hash = _load_qbittorrent_torrents(args)
    if not torrents_by_hash:
        return HealthDecisionPlan()

    settings = _health_policy_from_args(args)
    now_ts = now_unix_ts()
    decisions: list[HealthDecision] = []
    replace_candidates: list[HealthCandidate] = []
    race_candidates: list[HealthCandidate] = []

    for item in queue:
        candidate = _build_health_candidate(app_label, item_label, item, torrents_by_hash, now_ts)
        if candidate is None:
            continue
        if not _health_item_allowed(args, app_label, candidate.item_id):
            continue
        if (
            _release_availability_for_item(
                client, app_label, item_label, candidate.item_id, args.health_min_seeders
            )
            == "none"
        ):
            continue

        state_key = f"{app_label}:{candidate.item_id}"
        decision = evaluate_candidate(candidate, settings, state_store.state_for(state_key), now_ts)
        decisions.append(decision)
        log.info(
            "[%s][health] %s=%s action=%s age_h=%.1f avg_kib=%.1f progress_pct=%.1f reason=%s",
            app_label,
            item_label,
            candidate.item_id,
            decision.action,
            candidate.age_seconds / 3600,
            candidate.average_download_bps / 1024,
            candidate.progress * 100,
            ",".join(decision.reason_codes) or "n/a",
        )
        if decision.action == "replace":
            replace_candidates.append(candidate)
        elif decision.action == "race":
            race_candidates.append(candidate)

    plan = HealthDecisionPlan(
        decisions=decisions,
        replace_candidates=sorted(replace_candidates, key=lambda c: c.age_seconds, reverse=True),
        race_candidates=sorted(race_candidates, key=lambda c: c.age_seconds, reverse=True),
    )
    return apply_action_budget(plan, min(health_actions_remaining, settings.max_actions_per_sweep))


def _load_qbittorrent_torrents(args: argparse.Namespace) -> dict[str, QBTorrent]:
    try:
        qb = QBittorrentClient(
            base_url=args.qbittorrent_url,
            username=args.qbittorrent_username,
            password=args.qbittorrent_password,
            timeout_seconds=args.http_timeout_seconds,
        )
        qb.login()
        torrents = qb.torrents_info()
    except Exception as exc:  # noqa: BLE001
        log.warning("[health][warn] qBittorrent lookup failed: %s", exc)
        return {}
    return {torrent.torrent_hash: torrent for torrent in torrents}


def _health_item_allowed(args: argparse.Namespace, app_label: str, item_id: int) -> bool:
    if app_label == "sonarr" and args.health_episode_id:
        return item_id in set(args.health_episode_id)
    if app_label == "radarr" and args.health_movie_id:
        return item_id in set(args.health_movie_id)
    return True


def _extract_download_id(queue_item: dict) -> str:
    for key in ("downloadId", "trackedDownloadId", "downloadClientId"):
        raw = queue_item.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
    return ""


def _build_health_candidate(
    app_label: str,
    item_label: str,
    queue_item: dict,
    torrents_by_hash: dict[str, QBTorrent],
    now_ts: int,
) -> HealthCandidate | None:
    item_id = queue_item.get(item_label)
    queue_id = queue_item.get("id")
    if not isinstance(item_id, int) or not isinstance(queue_id, int):
        return None
    download_id = _extract_download_id(queue_item)
    if not download_id:
        return None
    torrent = torrents_by_hash.get(download_id)
    if torrent is None:
        return None
    if torrent.is_complete:
        return None
    if queue_item.get("protocol") != "torrent":
        return None

    age_seconds = max(0, now_ts - torrent.added_on)
    return HealthCandidate(
        app_label=app_label,
        item_id=item_id,
        queue_id=queue_id,
        title=str(queue_item.get("title") or torrent.name),
        download_id=download_id,
        age_seconds=age_seconds,
        average_download_bps=torrent.average_download_bps,
        progress=torrent.progress,
    )


def _release_availability_for_item(
    client: ArrClient,
    app_label: str,
    item_label: str,
    item_id: int,
    min_seeders: int,
) -> str:
    query_key = "episodeId" if item_label == "episodeId" else "movieId"
    releases = _safe_release_lookup(client, {query_key: item_id}, app_label, item_label, item_id)
    has_approved, has_seeded = analyze_releases(releases, min_seeders)
    if has_approved:
        return "approved"
    if has_seeded:
        return "seeded"
    return "none"


def _remove_health_replace_queue_items(client: ArrClient, items: list[HealthCandidate], app_label: str) -> None:
    for candidate in items:
        try:
            client.delete(
                f"/queue/{candidate.queue_id}",
                {
                    "removeFromClient": "true",
                    "blocklist": "true",
                    "skipRedownload": "false",
                },
            )
            log.info(
                "[%s][ok] removed low-health queue item %s %s", app_label, candidate.queue_id, candidate.title
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[%s][warn] remove failed for low-health queue %s: %s", app_label, candidate.queue_id, exc
            )


def _record_health_actions(
    state_store: HealthStateStore,
    app_label: str,
    plan: HealthDecisionPlan,
    now_ts: int,
) -> None:
    for candidate in plan.replace_candidates:
        state = state_store.state_for(f"{app_label}:{candidate.item_id}")
        state.replacement_timestamps.append(now_ts)
        state.prune(now_ts)
    for candidate in plan.race_candidates:
        state = state_store.state_for(f"{app_label}:{candidate.item_id}")
        state.race_timestamps.append(now_ts)
        state.prune(now_ts)
