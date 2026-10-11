from __future__ import annotations

from typing import Any

from homelab_workers.shared.arr import (
    ArrClient,
    _arr_queue_record_active,
    _arr_queue_records_for,
    _queue_record_download_ids,
)
from homelab_workers.shared.release_formatting import int_field
from homelab_workers.shared.sonarr_releases import _episodes_in_season


def _norm_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _find_library_match(
    row: dict[str, Any],
    library: list[Any],
    id_keys: tuple[str, ...],
) -> dict[str, Any] | None:
    for lib_row in library:
        if not isinstance(lib_row, dict):
            continue
        for key in id_keys:
            row_val = _norm_id(row.get(key))
            lib_val = _norm_id(lib_row.get(key))
            if row_val is not None and lib_val is not None and row_val == lib_val:
                return lib_row
        row_id = int_field(row.get("id"))
        lib_id = int_field(lib_row.get("id"))
        if row_id is not None and lib_id is not None and row_id == lib_id:
            return lib_row
    return None


def _series_has_file(lib_row: dict[str, Any]) -> bool | None:
    stats = lib_row.get("statistics")
    if isinstance(stats, dict):
        count = int_field(stats.get("episodeFileCount"))
        if count is not None:
            return count > 0
    return None


def _shape_movie_candidate(row: dict[str, Any], lib_match: dict[str, Any] | None) -> dict[str, Any]:
    in_library = lib_match is not None
    return {
        "title": str(row.get("title") or "Unknown"),
        "year": int_field(row.get("year")),
        "tmdb_id": int_field(row.get("tmdbId")),
        "imdb_id": _norm_id(row.get("imdbId")),
        "in_library": in_library,
        "library_id": int_field(lib_match.get("id")) if lib_match else None,
        "has_file": bool(lib_match.get("hasFile")) if lib_match else None,
    }


def _shape_series_candidate(row: dict[str, Any], lib_match: dict[str, Any] | None) -> dict[str, Any]:
    in_library = lib_match is not None
    return {
        "title": str(row.get("title") or row.get("cleanTitle") or "Unknown"),
        "year": int_field(row.get("year")),
        "tvdb_id": int_field(row.get("tvdbId")),
        "tmdb_id": int_field(row.get("tmdbId")),
        "imdb_id": _norm_id(row.get("imdbId")),
        "in_library": in_library,
        "library_id": int_field(lib_match.get("id")) if lib_match else None,
        "has_file": _series_has_file(lib_match) if lib_match else None,
    }


def arr_lookup(client: ArrClient, *, media_type: str, query: str) -> dict[str, Any]:
    mt = media_type.strip().lower()
    q = " ".join(query.split())
    if mt not in {"movie", "series"}:
        return {
            "ok": False,
            "error": {"code": "INVALID_ARGUMENT", "message": "media_type must be movie|series"},
        }

    if mt == "movie":
        rows = client.get("/movie/lookup", {"term": q})
        if not isinstance(rows, list):
            return {
                "ok": False,
                "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "movie lookup response is not a list"},
            }
        if not rows:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": "no lookup candidates"}}

        library = client.get("/movie")
        library_rows = library if isinstance(library, list) else []
        candidates = [
            _shape_movie_candidate(row, _find_library_match(row, library_rows, ("tmdbId", "imdbId")))
            for row in rows
            if isinstance(row, dict)
        ]
    else:
        rows = client.get("/series/lookup", {"term": q})
        if not isinstance(rows, list):
            return {
                "ok": False,
                "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "series lookup response is not a list"},
            }
        if not rows:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": "no lookup candidates"}}

        library = client.get("/series")
        library_rows = library if isinstance(library, list) else []
        candidates = [
            _shape_series_candidate(
                row,
                _find_library_match(row, library_rows, ("tvdbId", "tmdbId", "imdbId")),
            )
            for row in rows
            if isinstance(row, dict)
        ]

    if not candidates:
        return {"ok": False, "error": {"code": "NOT_FOUND", "message": "no lookup candidates"}}

    return {"ok": True, "media_type": mt, "query": q, "candidates": candidates}


def canonical_ensure_action(
    *,
    media_type: str,
    tmdb_id: int | None = None,
    tvdb_id: int | None = None,
    season: int | None = None,
) -> str:
    mt = media_type.strip().lower()
    if mt == "movie":
        return f"ensure:movie:{tmdb_id}"
    if mt == "series":
        stable_id = tvdb_id if tvdb_id is not None else tmdb_id
        return f"ensure:series:{stable_id}:s{season}"
    raise ValueError(f"unsupported media_type: {media_type}")


def _pick_quality_profile_id(profiles: list[Any]) -> int | None:
    for row in profiles:
        if isinstance(row, dict) and str(row.get("name") or "") == "HD-1080p":
            return int_field(row.get("id"))
    first = profiles[0] if profiles else None
    return int_field(first.get("id")) if isinstance(first, dict) else None


def _pick_root_folder_path(roots: list[Any]) -> str | None:
    first = roots[0] if roots else None
    if isinstance(first, dict) and first.get("path"):
        return str(first["path"])
    return None


def _find_movie_by_tmdb(library: list[Any], tmdb_id: int) -> dict[str, Any] | None:
    for row in library:
        if isinstance(row, dict) and int_field(row.get("tmdbId")) == tmdb_id:
            return row
    return None


def _find_series_by_external_ids(
    library: list[Any],
    *,
    tvdb_id: int | None,
    tmdb_id: int | None,
) -> dict[str, Any] | None:
    for row in library:
        if not isinstance(row, dict):
            continue
        if tvdb_id is not None and int_field(row.get("tvdbId")) == tvdb_id:
            return row
        if tmdb_id is not None and int_field(row.get("tmdbId")) == tmdb_id:
            return row
    return None


def _ensure_movie_monitored(client: ArrClient, movie: dict[str, Any]) -> dict[str, Any]:
    movie_id = int_field(movie.get("id"))
    if movie_id is None:
        return {
            "ok": False,
            "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "movie missing id"},
        }
    if movie.get("monitored") is not True:
        updated = dict(movie)
        updated["monitored"] = True
        movie = client.put(f"/movie/{movie_id}", updated)
        if not isinstance(movie, dict):
            return {
                "ok": False,
                "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "bad movie update response"},
            }
    return {
        "ok": True,
        "created": False,
        "library_id": movie_id,
        "path": movie.get("path"),
        "monitored": movie.get("monitored"),
    }


def _add_movie_from_lookup(client: ArrClient, tmdb_id: int) -> dict[str, Any]:
    lookup_rows = client.get("/movie/lookup", {"term": f"tmdb:{tmdb_id}"})
    if not isinstance(lookup_rows, list) or not lookup_rows:
        return {"ok": False, "error": {"code": "NOT_FOUND", "message": "movie lookup returned empty"}}
    candidate = lookup_rows[0]
    if not isinstance(candidate, dict):
        return {
            "ok": False,
            "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "movie lookup candidate is not a dict"},
        }

    roots = client.get("/rootfolder")
    root_rows = roots if isinstance(roots, list) else []
    root_path = _pick_root_folder_path(root_rows)
    if not root_path:
        return {"ok": False, "error": {"code": "NO_ROOT_FOLDER", "message": "no root folders configured"}}

    profiles = client.get("/qualityprofile")
    profile_rows = profiles if isinstance(profiles, list) else []
    profile_id = _pick_quality_profile_id(profile_rows)
    if profile_id is None:
        return {
            "ok": False,
            "error": {"code": "NO_QUALITY_PROFILE", "message": "no quality profiles configured"},
        }

    payload = {
        **candidate,
        "qualityProfileId": profile_id,
        "rootFolderPath": root_path,
        "monitored": True,
        "minimumAvailability": "released",
        "addOptions": {"searchForMovie": False},
    }
    added = client.post("/movie", payload)
    if not isinstance(added, dict):
        return {
            "ok": False,
            "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "bad movie add response"},
        }
    library_id = int_field(added.get("id"))
    if library_id is None:
        return {
            "ok": False,
            "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "added movie missing id"},
        }
    return {
        "ok": True,
        "created": True,
        "library_id": library_id,
        "path": added.get("path"),
        "monitored": added.get("monitored"),
    }


def _monitor_series_season(client: ArrClient, series_id: int, season_number: int) -> dict[str, Any] | None:
    season_episodes = _episodes_in_season(client, series_id, season_number)
    if not season_episodes:
        return {
            "ok": False,
            "error": {
                "code": "NO_EPISODES_FOR_SEASON",
                "message": f"No episodes for S{season_number:02d}; refresh series metadata first.",
            },
        }

    episode_ids = [eid for eid in (int_field(ep.get("id")) for ep in season_episodes) if eid is not None]
    if episode_ids:
        client.put("/episode/monitor", {"episodeIds": episode_ids, "monitored": True})

    detail = client.get(f"/series/{series_id}")
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
        client.put(f"/series/{series_id}", detail)
    return None


def _add_series_from_lookup(
    client: ArrClient,
    *,
    tvdb_id: int | None,
    tmdb_id: int | None,
    season: int,
) -> dict[str, Any]:
    if tvdb_id is not None:
        term = f"tvdb:{tvdb_id}"
    elif tmdb_id is not None:
        term = f"tmdb:{tmdb_id}"
    else:
        return {
            "ok": False,
            "error": {"code": "INVALID_ARGUMENT", "message": "tvdb_id or tmdb_id required for series add"},
        }

    lookup_rows = client.get("/series/lookup", {"term": term})
    if not isinstance(lookup_rows, list) or not lookup_rows:
        return {"ok": False, "error": {"code": "NOT_FOUND", "message": "series lookup returned empty"}}
    candidate = lookup_rows[0]
    if not isinstance(candidate, dict):
        return {
            "ok": False,
            "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "series lookup candidate is not a dict"},
        }

    roots = client.get("/rootfolder")
    root_rows = roots if isinstance(roots, list) else []
    root_path = _pick_root_folder_path(root_rows)
    if not root_path:
        return {"ok": False, "error": {"code": "NO_ROOT_FOLDER", "message": "no root folders configured"}}

    profiles = client.get("/qualityprofile")
    profile_rows = profiles if isinstance(profiles, list) else []
    profile_id = _pick_quality_profile_id(profile_rows)
    if profile_id is None:
        return {
            "ok": False,
            "error": {"code": "NO_QUALITY_PROFILE", "message": "no quality profiles configured"},
        }

    seasons = candidate.get("seasons")
    if isinstance(seasons, list):
        for s in seasons:
            if isinstance(s, dict) and int_field(s.get("seasonNumber")) == season:
                s["monitored"] = True

    payload = {
        **candidate,
        "qualityProfileId": profile_id,
        "rootFolderPath": root_path,
        "monitored": True,
        "seasonFolder": True,
        "addOptions": {"searchForMissingEpisodes": False},
    }
    added = client.post("/series", payload)
    if not isinstance(added, dict):
        return {
            "ok": False,
            "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "bad series add response"},
        }
    library_id = int_field(added.get("id"))
    if library_id is None:
        return {
            "ok": False,
            "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "added series missing id"},
        }

    monitor_err = _monitor_series_season(client, library_id, season)
    if monitor_err is not None:
        return monitor_err

    return {
        "ok": True,
        "created": True,
        "library_id": library_id,
        "path": added.get("path"),
        "monitored": added.get("monitored"),
        "season": season,
    }


def arr_ensure(
    client: ArrClient,
    *,
    media_type: str,
    tmdb_id: int | None = None,
    tvdb_id: int | None = None,
    library_id: int | None = None,
    season: int | None = None,
) -> dict[str, Any]:
    mt = media_type.strip().lower()
    if mt not in {"movie", "series"}:
        return {
            "ok": False,
            "error": {"code": "INVALID_ARGUMENT", "message": "media_type must be movie|series"},
        }

    if mt == "movie":
        if library_id is not None:
            movie = client.get(f"/movie/{library_id}")
            if not isinstance(movie, dict):
                return {
                    "ok": False,
                    "error": {"code": "UNKNOWN_LIBRARY_ID", "message": f"No movie with id {library_id}"},
                }
            result = _ensure_movie_monitored(client, movie)
        elif tmdb_id is None:
            return {
                "ok": False,
                "error": {"code": "INVALID_ARGUMENT", "message": "tmdb_id required unless library_id provided"},
            }
        else:
            library = client.get("/movie")
            library_rows = library if isinstance(library, list) else []
            existing = _find_movie_by_tmdb(library_rows, tmdb_id)
            if existing is not None:
                result = _ensure_movie_monitored(client, existing)
            else:
                result = _add_movie_from_lookup(client, tmdb_id)
        if not result.get("ok"):
            return result
        return {"ok": True, "media_type": "movie", **result}

    if season is None:
        return {
            "ok": False,
            "error": {"code": "INVALID_ARGUMENT", "message": "season required for series ensure"},
        }

    if library_id is not None:
        series = client.get(f"/series/{library_id}")
        if not isinstance(series, dict) or int_field(series.get("id")) is None:
            return {
                "ok": False,
                "error": {"code": "UNKNOWN_LIBRARY_ID", "message": f"No series with id {library_id}"},
            }
        created = False
    elif tvdb_id is None and tmdb_id is None:
        return {
            "ok": False,
            "error": {"code": "INVALID_ARGUMENT", "message": "tvdb_id or tmdb_id required unless library_id provided"},
        }
    else:
        library = client.get("/series")
        library_rows = library if isinstance(library, list) else []
        series = _find_series_by_external_ids(library_rows, tvdb_id=tvdb_id, tmdb_id=tmdb_id)
        if series is None:
            add_result = _add_series_from_lookup(client, tvdb_id=tvdb_id, tmdb_id=tmdb_id, season=season)
            if not add_result.get("ok"):
                return add_result
            return {"ok": True, "media_type": "series", **add_result}
        created = False

    series_id = int_field(series.get("id"))
    if series_id is None:
        return {
            "ok": False,
            "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "series missing id"},
        }

    monitor_err = _monitor_series_season(client, series_id, season)
    if monitor_err is not None:
        return monitor_err

    return {
        "ok": True,
        "media_type": "series",
        "created": created,
        "library_id": series_id,
        "path": series.get("path"),
        "monitored": True,
        "season": season,
    }


def _resolve_library_id_from_query(
    client: ArrClient,
    *,
    media_type: str,
    query: str,
) -> tuple[int | None, dict[str, Any] | None]:
    lookup = arr_lookup(client, media_type=media_type, query=query)
    if not lookup.get("ok"):
        return None, lookup
    for candidate in lookup.get("candidates") or []:
        if isinstance(candidate, dict) and candidate.get("in_library"):
            lid = int_field(candidate.get("library_id"))
            if lid is not None:
                return lid, None
    return None, {"ok": False, "error": {"code": "NOT_FOUND", "message": "query not in library"}}


def _movie_quality(movie: dict[str, Any]) -> str | None:
    movie_file = movie.get("movieFile")
    if not isinstance(movie_file, dict):
        return None
    quality = movie_file.get("quality")
    if isinstance(quality, dict):
        q = quality.get("quality")
        if isinstance(q, dict) and q.get("name"):
            return str(q["name"])
    return None


def _filter_queue_by_season(
    records: list[dict[str, Any]],
    season: int | None,
) -> list[dict[str, Any]]:
    if season is None:
        return records
    filtered: list[dict[str, Any]] = []
    for rec in records:
        sn = int_field(rec.get("seasonNumber"))
        if sn is None or sn == season:
            filtered.append(rec)
    return filtered


def _history_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    return [row for row in (payload.get("records") or []) if isinstance(row, dict)]


def _selected_file(
    file_row: Any,
    *,
    season: int | None,
    episode: int | None,
    title: str,
) -> dict[str, Any] | None:
    if not isinstance(file_row, dict):
        return None
    path = file_row.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    return {
        "path": path,
        "season": season,
        "episode": episode,
        "title": title,
        "size": int_field(file_row.get("size")),
    }


def _movie_selected_files(
    client: ArrClient,
    movie: dict[str, Any],
    title: str,
) -> list[dict[str, Any]]:
    if not movie.get("hasFile"):
        return []
    movie_file = movie.get("movieFile")
    if not isinstance(movie_file, dict):
        return []
    movie_file_id = int_field(movie_file.get("id"))
    if movie_file_id is None:
        return []
    file_rows = client.get("/moviefile", {"movieId": movie.get("id")})
    if not isinstance(file_rows, list):
        return []
    for file_row in file_rows:
        if not isinstance(file_row, dict) or int_field(file_row.get("id")) != movie_file_id:
            continue
        selected = _selected_file(file_row, season=None, episode=None, title=title)
        return [selected] if selected is not None else []
    return []


def _series_selected_files(
    client: ArrClient,
    episodes: list[dict[str, Any]],
    season: int | None,
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    seen_file_ids: set[int] = set()
    selected_episodes = sorted(
        (
            episode
            for episode in episodes
            if season is None or int_field(episode.get("seasonNumber")) == season
        ),
        key=lambda episode: (
            int_field(episode.get("seasonNumber")) or -1,
            int_field(episode.get("episodeNumber")) or -1,
        ),
    )
    for episode in selected_episodes:
        if not episode.get("hasFile"):
            continue
        file_id = int_field(episode.get("episodeFileId"))
        if file_id is None or file_id in seen_file_ids:
            continue
        seen_file_ids.add(file_id)
        response = client.get_response(f"/episodefile/{file_id}")
        if response.status_code == 404:
            continue
        selected = _selected_file(
            response.raise_for_status().json(),
            season=int_field(episode.get("seasonNumber")),
            episode=int_field(episode.get("episodeNumber")),
            title=str(episode.get("title") or "Unknown"),
        )
        if selected is not None:
            files.append(selected)
    return files


def _compact_queue_row(rec: dict[str, Any]) -> dict[str, Any]:
    size = int(rec.get("size") or 0)
    sizeleft = int(rec.get("sizeleft") or 0)
    progress_pct = None
    if size > 0:
        progress_pct = round(max(0.0, min(100.0, 100.0 * (size - sizeleft) / size)), 1)
    return {
        "id": rec.get("id"),
        "title": rec.get("title"),
        "status": rec.get("status"),
        "tracked_download_status": rec.get("trackedDownloadStatus"),
        "download_ids": _queue_record_download_ids(rec),
        "size_bytes": size,
        "sizeleft_bytes": sizeleft,
        "progress_pct": progress_pct,
        "active": _arr_queue_record_active(rec),
    }


def _compact_history_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventType": row.get("eventType"),
        "sourceTitle": row.get("sourceTitle"),
        "date": row.get("date"),
    }


def arr_status(
    client: ArrClient,
    *,
    media_type: str,
    library_id: int | None = None,
    query: str | None = None,
    season: int | None = None,
) -> dict[str, Any]:
    mt = media_type.strip().lower()
    if mt not in {"movie", "series"}:
        return {
            "ok": False,
            "error": {"code": "INVALID_ARGUMENT", "message": "media_type must be movie|series"},
        }

    resolved_id = library_id
    if resolved_id is None:
        if not query or not query.strip():
            return {
                "ok": False,
                "error": {"code": "INVALID_ARGUMENT", "message": "library_id or query required"},
            }
        resolved_id, err = _resolve_library_id_from_query(client, media_type=mt, query=query)
        if err is not None:
            return err
        if resolved_id is None:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": "query not in library"}}

    if mt == "movie":
        item = client.get(f"/movie/{resolved_id}")
        if not isinstance(item, dict):
            return {
                "ok": False,
                "error": {"code": "UNKNOWN_LIBRARY_ID", "message": f"No movie with id {resolved_id}"},
            }
        queue_payload = client.queue()
        queue_rows = _arr_queue_records_for(queue_payload, resolved_id, "movieId")
        history_payload = client.get("/history", {"movieIds": resolved_id, "pageSize": 10})
        has_file = bool(item.get("hasFile"))
        quality = _movie_quality(item)
        title = str(item.get("title") or "Unknown")
        files = _movie_selected_files(client, item, title)
    else:
        item = client.get(f"/series/{resolved_id}")
        if not isinstance(item, dict):
            return {
                "ok": False,
                "error": {"code": "UNKNOWN_LIBRARY_ID", "message": f"No series with id {resolved_id}"},
            }
        queue_payload = client.queue()
        queue_rows = _filter_queue_by_season(
            _arr_queue_records_for(queue_payload, resolved_id, "seriesId"),
            season,
        )
        history_query: dict[str, object] = {"seriesIds": resolved_id, "pageSize": 10}
        if season is not None:
            history_query["seasonNumber"] = season
        history_payload = client.get("/history", history_query)
        episode_query: dict[str, object] = {"seriesId": resolved_id}
        if season is not None:
            episode_query["seasonNumber"] = season
        episode_payload = client.get("/episode", episode_query)
        episodes = [row for row in episode_payload if isinstance(row, dict)] if isinstance(episode_payload, list) else []
        if season is not None:
            has_file = any(
                bool(episode.get("hasFile"))
                and int_field(episode.get("seasonNumber")) == season
                for episode in episodes
            )
        else:
            series_file = _series_has_file(item)
            has_file = series_file
        quality = None
        title = str(item.get("title") or item.get("sortTitle") or "Unknown")
        files = _series_selected_files(client, episodes, season)

    return {
        "ok": True,
        "media_type": mt,
        "library_id": resolved_id,
        "title": title,
        "has_file": has_file,
        "quality": quality,
        "queue": [_compact_queue_row(row) for row in queue_rows[:25]],
        "history": [_compact_history_row(row) for row in _history_records(history_payload)[:10]],
        "files": files,
    }


def arr_queue_remove(
    client: ArrClient,
    *,
    queue_id: int,
    remove_from_client: bool = True,
    blocklist: bool = False,
) -> dict[str, Any]:
    client.delete(
        f"/queue/{queue_id}",
        query={
            "removeFromClient": remove_from_client,
            "blocklist": blocklist,
            "skipRedownload": True,
        },
    )
    return {
        "ok": True,
        "removed": True,
        "queue_id": queue_id,
        "remove_from_client": remove_from_client,
        "blocklist": blocklist,
    }
