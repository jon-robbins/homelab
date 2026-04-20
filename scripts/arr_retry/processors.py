from __future__ import annotations

import argparse
from collections.abc import Iterable

from .client import ArrClient, read_api_key_from_config_xml
from .logic import RetryPlan, analyze_releases, choose_force_grab_candidate, queue_item_looks_stalled


def process_sonarr(args: argparse.Namespace, dry_run: bool) -> int:
    key = args.sonarr_api_key or read_api_key_from_config_xml(args.sonarr_config_path)
    if not key:
        print("[sonarr] No API key found; skipping.")
        return 0

    client = ArrClient(args.sonarr_url, key, args.http_timeout_seconds)
    queue = _load_queue(client, "sonarr")
    stalled = [item for item in queue if queue_item_looks_stalled(item)]
    queue_episode_ids = {item.get("episodeId") for item in queue if isinstance(item.get("episodeId"), int)}
    print(f"[sonarr] queue={len(queue)} stalled={len(stalled)}")

    plan = RetryPlan()
    if args.search_missing_monitored:
        _plan_sonarr_missing(args, client, queue_episode_ids, plan)
    _add_stalled_sonarr_items(stalled, plan)

    plan.cap(args.max_searches)
    print(f"[sonarr] retries planned={len(plan.search_ids)}")
    if args.allow_force_grab_fallback:
        print(f"[sonarr] force-grab planned={len(plan.force_grab_candidates)}")

    if dry_run:
        return plan.total

    _remove_stalled_queue_items(client, stalled, "sonarr")
    _run_searches(client, sorted(plan.search_ids), "sonarr", "EpisodeSearch", "episodeId", "episodeIds")
    _run_force_grabs(client, sorted(plan.force_grab_candidates.items()), "sonarr", "episodeId")
    return plan.total


def process_radarr(args: argparse.Namespace, dry_run: bool) -> int:
    if args.no_radarr:
        print("[radarr] Skipped by --no-radarr")
        return 0

    key = args.radarr_api_key or read_api_key_from_config_xml(args.radarr_config_path)
    if not key:
        print("[radarr] No API key found; skipping.")
        return 0

    client = ArrClient(args.radarr_url, key, args.http_timeout_seconds)
    queue = _load_queue(client, "radarr")
    stalled = [item for item in queue if queue_item_looks_stalled(item)]
    queue_movie_ids = {item.get("movieId") for item in queue if isinstance(item.get("movieId"), int)}
    print(f"[radarr] queue={len(queue)} stalled={len(stalled)}")

    plan = RetryPlan()
    if args.search_missing_monitored:
        _plan_radarr_missing(args, client, queue_movie_ids, plan)
    _add_stalled_radarr_items(stalled, plan)

    plan.cap(args.max_searches)
    print(f"[radarr] retries planned={len(plan.search_ids)}")
    if args.allow_force_grab_fallback:
        print(f"[radarr] force-grab planned={len(plan.force_grab_candidates)}")

    if dry_run:
        return plan.total

    _remove_stalled_queue_items(client, stalled, "radarr")
    _run_searches(client, sorted(plan.search_ids), "radarr", "MoviesSearch", "movieId", "movieIds")
    _run_force_grabs(client, sorted(plan.force_grab_candidates.items()), "radarr", "movieId")
    return plan.total


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
        print(f"[{app_label}][warn] release lookup failed {item_label}={item_id}: {exc}")
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
        print(
            f"[{app_label}][stuck+available] {item_label}={item_id} "
            f"title={title} has approved release(s) but no active queue item"
        )
        plan.search_ids.add(item_id)
        return

    if not has_seeded:
        return

    print(f"[{app_label}][available-but-filtered] {item_label}={item_id} {rejected_suffix}")
    if not args.allow_force_grab_fallback:
        return

    candidate = choose_force_grab_candidate(releases, args.min_seeders)
    if candidate is None:
        return

    plan.force_grab_candidates[item_id] = candidate
    print(
        f"[{app_label}][force-grab-candidate] {item_label}={item_id} "
        f"seeders={candidate.get('seeders')} title={candidate.get('title')}"
    )


def _add_stalled_sonarr_items(stalled: list[dict], plan: RetryPlan) -> None:
    for item in stalled:
        episode_id = item.get("episodeId")
        if isinstance(episode_id, int):
            plan.search_ids.add(episode_id)
        print(
            f"[sonarr][stalled] queueId={item.get('id')} episodeId={item.get('episodeId')} "
            f"title={item.get('title')}"
        )


def _add_stalled_radarr_items(stalled: list[dict], plan: RetryPlan) -> None:
    for item in stalled:
        movie_id = item.get("movieId")
        if isinstance(movie_id, int):
            plan.search_ids.add(movie_id)
        print(
            f"[radarr][stalled] queueId={item.get('id')} movieId={item.get('movieId')} "
            f"title={item.get('title')}"
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
            print(f"[{app_label}][ok] removed stalled queue item {queue_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{app_label}][warn] remove failed for queue {queue_id}: {exc}")


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
            print(f"[{app_label}][ok] {command_name} {log_key}={item_id} commandId={command_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{app_label}][warn] {command_name} failed for {log_key}={item_id}: {exc}")


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
            print(
                f"[{app_label}][ok] force-grabbed {item_label}={item_id} "
                f"title={release.get('title')} releaseId={release_id}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[{app_label}][warn] force-grab failed for {item_label}={item_id}: {exc}")
