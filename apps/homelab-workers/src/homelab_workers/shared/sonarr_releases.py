from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.radarr_releases import ReleaseSearchConfig
from homelab_workers.shared.release_formatting import (
    fold_for_match,
    human_size,
    indexer_name,
    int_field,
    release_flag_fields,
)


@dataclass(frozen=True)
class TVContext:
    series_id: int
    series_title: str
    season_number: int
    series_type: str


@dataclass(frozen=True)
class SonarrReleaseSearchConfig(ReleaseSearchConfig):
    max_episode_release_lookups: int = 15


def resolve_series_in_library(
    client: ArrClient, query: str, explicit_id: int | None
) -> tuple[TVContext | None, list[dict[str, Any]] | None, str | None]:
    if explicit_id is not None:
        series = client.get(f"/series/{explicit_id}")
        if not isinstance(series, dict) or int_field(series.get("id")) is None:
            return None, None, "UNKNOWN_SERIES_ID"
        title = str(series.get("title") or series.get("sortTitle") or "Unknown")
        series_type = str(series.get("seriesType") or "standard")
        return TVContext(explicit_id, title, 0, series_type), None, None

    parse_title = f"{query.strip()} S01E01"
    parsed = client.get("/parse", {"title": parse_title})
    if isinstance(parsed, dict) and parsed.get("episodes"):
        ser = parsed.get("series")
        if isinstance(ser, dict) and int_field(ser.get("id")) is not None:
            sid = int(ser["id"])
            title = str(ser.get("title") or ser.get("sortTitle") or "Unknown")
            detail = client.get(f"/series/{sid}")
            series_type = (
                str(detail.get("seriesType") or "standard")
                if isinstance(detail, dict)
                else "standard"
            )
            return TVContext(sid, title, 0, series_type), None, None

    rows = client.get("/series")
    if not isinstance(rows, list):
        return None, None, "UPSTREAM_BAD_RESPONSE"
    folded = fold_for_match(query)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = int_field(row.get("id"))
        if sid is None:
            continue
        title = str(row.get("title") or "")
        sort_title = str(row.get("sortTitle") or "")
        if folded and (
            folded in fold_for_match(title)
            or folded in fold_for_match(sort_title)
            or fold_for_match(title) in folded
        ):
            candidates.append({"id": sid, "title": title, "year": row.get("year")})
    if not candidates:
        return None, None, "SERIES_NOT_IN_LIBRARY"
    if len(candidates) == 1:
        row = candidates[0]
        detail = client.get(f"/series/{row['id']}")
        series_type = (
            str(detail.get("seriesType") or "standard")
            if isinstance(detail, dict)
            else "standard"
        )
        return (
            TVContext(int(row["id"]), str(row["title"]), 0, series_type),
            None,
            None,
        )
    return None, candidates, "AMBIGUOUS_SERIES"


def _episodes_in_season(client: ArrClient, series_id: int, season_number: int) -> list[dict[str, Any]]:
    data = client.get("/episode", {"seriesId": series_id, "seasonNumber": season_number})
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _s1e1_episode_id(client: ArrClient, series_id: int) -> int | None:
    for episode in _episodes_in_season(client, series_id, 1):
        if int_field(episode.get("episodeNumber")) == 1:
            return int_field(episode.get("id"))
    return None


def _select_episode_ids_for_lookups(
    season_episodes: list[dict[str, Any]],
    s1e1_id: int | None,
    include_full_series_packs: bool,
    current_season: int,
    cap: int,
) -> list[int]:
    ordered = sorted(season_episodes, key=lambda ep: int_field(ep.get("episodeNumber")) or 0)
    episode_ids = [int_field(ep.get("id")) for ep in ordered]
    episode_ids = [ep_id for ep_id in episode_ids if ep_id is not None]
    selected: list[int] = []
    if include_full_series_packs and current_season > 1 and s1e1_id is not None:
        selected.append(s1e1_id)
    if not episode_ids:
        return selected[:cap]
    if len(episode_ids) + len(selected) <= cap:
        for ep_id in episode_ids:
            if ep_id not in selected:
                selected.append(ep_id)
        return selected[:cap]
    need = cap - len(selected)
    total = len(episode_ids)
    for index in range(need):
        if need == 1:
            pick = 0
        else:
            pick = int(round(index * (total - 1) / (need - 1)))
        ep_id = episode_ids[pick]
        if ep_id not in selected:
            selected.append(ep_id)
    return selected[:cap]


def _releases_for_episode(client: ArrClient, episode_id: int) -> list[dict[str, Any]]:
    data = client.get("/release", {"episodeId": episode_id})
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _merge_releases(by_episode: list[tuple[int, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    by_guid: dict[str, dict[str, Any]] = {}
    for episode_id, releases in by_episode:
        for rel in releases:
            guid = str(rel.get("guid") or "")
            if not guid:
                guid = f"h:{episode_id}|{rel.get('title')}|{rel.get('indexer')}|{rel.get('size')}"
            if guid not in by_guid:
                by_guid[guid] = {**rel, "_episodeId": episode_id}
    merged = list(by_guid.values())
    merged.sort(
        key=lambda row: (
            -(int_field(row.get("seeders")) or 0),
            -(int_field(row.get("leechers")) or 0),
            -(int_field(row.get("size")) or 0),
        )
    )
    return merged


def _search_then_poll_releases(
    client: ArrClient,
    ctx: TVContext,
    season_number: int,
    episode_ids: list[int],
    config: SonarrReleaseSearchConfig,
) -> tuple[list[dict[str, Any]], str]:
    series_type = ctx.series_type.lower()
    if series_type == "anime" and episode_ids:
        for chunk in [episode_ids[i : i + 30] for i in range(0, len(episode_ids), 30)]:
            client.post("/command", {"name": "EpisodeSearch", "episodeIds": chunk})
        note = "EpisodeSearch (anime)"
    else:
        client.post(
            "/command",
            {
                "name": "SeasonSearch",
                "seriesId": ctx.series_id,
                "seasonNumber": season_number,
            },
        )
        note = "SeasonSearch"
    deadline = time.monotonic() + config.search_timeout_s
    merged: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        by_episode = [(ep_id, _releases_for_episode(client, ep_id)) for ep_id in episode_ids]
        merged = _merge_releases(by_episode)
        if merged:
            break
        time.sleep(config.poll_s)
    return merged, note


def _shape_tv_option(rank: int, rel: dict[str, Any]) -> dict[str, Any]:
    size = int_field(rel.get("size")) or 0
    return {
        "rank": rank,
        "title": str(rel.get("title") or "Unknown release"),
        "seeders": int_field(rel.get("seeders")) or 0,
        "leechers": int_field(rel.get("leechers")) or 0,
        "size_bytes": size,
        "size_human": human_size(size),
        "indexer": indexer_name(rel),
        "guid": str(rel.get("guid") or ""),
        "episode_id": int_field(rel.get("_episodeId")),
        "approved": bool(rel.get("approved")),
        "download_allowed": bool(rel.get("downloadAllowed", True)),
        **release_flag_fields(rel),
    }


def search_sonarr_releases(
    client: ArrClient,
    *,
    query: str,
    season: int,
    series_id: int | None,
    include_full_series_packs: bool,
    config: SonarrReleaseSearchConfig,
) -> dict[str, Any]:
    if client.indexers_enabled_count() == 0:
        return {
            "ok": False,
            "error": {
                "code": "NO_ACTIVE_INDEXERS",
                "message": "Sonarr has zero enabled indexers.",
            },
        }
    normalized_query = " ".join(query.split())
    ctx, ambiguous, err = resolve_series_in_library(client, normalized_query, series_id)
    if err == "AMBIGUOUS_SERIES" and ambiguous is not None:
        return {
            "ok": False,
            "error": {
                "code": "AMBIGUOUS_SERIES",
                "message": "Multiple Sonarr series match; pass series_id.",
                "series_candidates": ambiguous,
            },
        }
    if err == "UNKNOWN_SERIES_ID":
        return {
            "ok": False,
            "error": {
                "code": "UNKNOWN_SERIES_ID",
                "message": f"No series with id {series_id} in Sonarr library.",
            },
        }
    if err == "SERIES_NOT_IN_LIBRARY":
        return {
            "ok": False,
            "error": {
                "code": "SERIES_NOT_IN_LIBRARY",
                "message": "Show not in Sonarr library; add the series first.",
            },
        }
    if err is not None or ctx is None:
        return {
            "ok": False,
            "error": {
                "code": err or "RESOLVE_FAILED",
                "message": "Could not resolve series in Sonarr.",
            },
        }
    detail = client.get(f"/series/{ctx.series_id}")
    if not isinstance(detail, dict):
        return {"ok": False, "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "bad series"}}
    ctx = TVContext(
        series_id=ctx.series_id,
        series_title=str(detail.get("title") or detail.get("sortTitle") or ctx.series_title),
        season_number=season,
        series_type=str(detail.get("seriesType") or "standard"),
    )
    season_episodes = _episodes_in_season(client, ctx.series_id, season)
    if not season_episodes:
        return {
            "ok": False,
            "error": {
                "code": "NO_EPISODES_FOR_SEASON",
                "message": f"No episodes for S{season:02d}; monitor the season in Sonarr.",
            },
        }
    s1e1 = _s1e1_episode_id(client, ctx.series_id) if include_full_series_packs else None
    episode_ids = _select_episode_ids_for_lookups(
        season_episodes,
        s1e1,
        include_full_series_packs,
        season,
        config.max_episode_release_lookups,
    )
    if not episode_ids:
        return {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "no episode ids"}}
    merged, search_note = _search_then_poll_releases(
        client, ctx, season, episode_ids, config
    )
    if not merged:
        return {
            "ok": False,
            "error": {
                "code": "NO_RELEASES",
                "message": (
                    f"No releases after {int(config.search_timeout_s)}s; check indexers and filters."
                ),
            },
        }
    limit = min(config.options_limit, 25)
    options = [_shape_tv_option(i, rel) for i, rel in enumerate(merged[:limit], start=1)]
    return {
        "ok": True,
        "type": "tv",
        "series": {"id": ctx.series_id, "title": ctx.series_title},
        "season": season,
        "query": normalized_query,
        "search": search_note,
        "options": options,
    }


def canonical_season_ensure(series_id: int, season_number: int) -> str:
    return f"sonarr-season-ensure:{series_id}:{season_number}"


def ensure_season_monitored_and_search(
    client: ArrClient,
    *,
    season_number: int,
    query: str | None = None,
    series_id: int | None = None,
) -> dict[str, Any]:
    """Monitor a Sonarr season + episodes, then queue SeasonSearch."""
    if series_id is None and not (query or "").strip():
        return {
            "ok": False,
            "error": {
                "code": "INVALID_ARGUMENT",
                "message": "Pass series_id or query.",
            },
        }
    ctx, ambiguous, err = resolve_series_in_library(
        client, (query or "").strip(), series_id
    )
    if err == "AMBIGUOUS_SERIES":
        return {
            "ok": False,
            "error": {
                "code": "AMBIGUOUS_SERIES",
                "message": "Multiple Sonarr series match; pass series_id.",
                "series_candidates": ambiguous,
            },
        }
    if err == "UNKNOWN_SERIES_ID":
        return {
            "ok": False,
            "error": {
                "code": "UNKNOWN_SERIES_ID",
                "message": f"No series with id {series_id} in Sonarr library.",
            },
        }
    if err == "SERIES_NOT_IN_LIBRARY":
        return {
            "ok": False,
            "error": {
                "code": "SERIES_NOT_IN_LIBRARY",
                "message": "Show not in Sonarr library; add the series first.",
            },
        }
    if err is not None or ctx is None:
        return {
            "ok": False,
            "error": {
                "code": err or "RESOLVE_FAILED",
                "message": "Could not resolve series in Sonarr.",
            },
        }

    season_episodes = _episodes_in_season(client, ctx.series_id, season_number)
    if not season_episodes:
        return {
            "ok": False,
            "error": {
                "code": "NO_EPISODES_FOR_SEASON",
                "message": f"No episodes for S{season_number:02d}; refresh series metadata first.",
            },
        }

    episode_ids = [eid for eid in (int_field(ep.get("id")) for ep in season_episodes) if eid is not None]
    previously_monitored = sum(1 for ep in season_episodes if ep.get("monitored") is True)
    client.put("/episode/monitor", {"episodeIds": episode_ids, "monitored": True})

    detail = client.get(f"/series/{ctx.series_id}")
    if not isinstance(detail, dict):
        return {
            "ok": False,
            "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "bad series payload"},
        }
    seasons = detail.get("seasons")
    if isinstance(seasons, list):
        for season in seasons:
            if not isinstance(season, dict):
                continue
            if int_field(season.get("seasonNumber")) == season_number:
                season["monitored"] = True
        detail["monitored"] = True
        client.put(f"/series/{ctx.series_id}", detail)

    command = client.post(
        "/command",
        {"name": "SeasonSearch", "seriesId": ctx.series_id, "seasonNumber": season_number},
    )
    if not isinstance(command, dict):
        command = {}

    return {
        "ok": True,
        "series": {"id": ctx.series_id, "title": ctx.series_title},
        "season": season_number,
        "episodes_total": len(episode_ids),
        "episodes_monitored": len(episode_ids),
        "episodes_newly_monitored": max(0, len(episode_ids) - previously_monitored),
        "command": {
            "id": command.get("id"),
            "name": command.get("name") or "SeasonSearch",
            "status": command.get("status"),
        },
        "expected_action": canonical_season_ensure(ctx.series_id, season_number),
    }


def grab_sonarr_release(client: ArrClient, *, episode_id: int, guid: str) -> dict[str, Any]:
    data = client.get("/release", {"episodeId": episode_id})
    if not isinstance(data, list):
        return {
            "ok": False,
            "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "sonarr /release is not a list"},
        }
    selected: dict[str, Any] | None = None
    for rel in data:
        if isinstance(rel, dict) and str(rel.get("guid") or "") == guid:
            selected = {k: v for k, v in rel.items() if not str(k).startswith("_")}
            break
    if not selected:
        return {
            "ok": False,
            "error": {
                "code": "RELEASE_GONE",
                "message": "Release no longer in Sonarr cache; run sonarr_release_search again.",
            },
        }
    response = client.post_response("/release", selected)
    try:
        body = response.json() if response.content else {}
    except Exception:  # noqa: BLE001
        body = {}
    if response.status_code >= 400:
        return {
            "ok": False,
            "error": {
                "code": "GRAB_FAILED",
                "message": f"sonarr status {response.status_code}",
            },
        }
    return {"ok": True, "app": "sonarr", "command": body}
