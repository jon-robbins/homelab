from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.release_formatting import (
    fold_for_match,
    human_size,
    indexer_name,
    int_field,
    release_flag_fields,
)


@dataclass(frozen=True)
class ReleaseSearchConfig:
    search_timeout_s: float = 30.0
    poll_s: float = 2.0
    options_limit: int = 10


def _movie_match_from_library(
    client: ArrClient, query: str, movie_id: int | None
) -> dict[str, Any]:
    rows = client.get("/movie")
    if not isinstance(rows, list):
        raise ValueError("RADARR_BAD")
    if movie_id is not None:
        for row in rows:
            if isinstance(row, dict) and int_field(row.get("id")) == movie_id:
                return row
        return {}
    folded = fold_for_match(query)
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "")
        original = str(row.get("originalTitle") or "")
        sort_title = str(row.get("sortTitle") or "")
        ft, fo, fs = fold_for_match(title), fold_for_match(original), fold_for_match(sort_title)
        if folded and (
            folded in ft or folded in fo or folded in fs or ft in folded or fo in folded or fs in folded
        ):
            candidates.append(row)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError("MOVIE_NOT_IN_LIBRARY")
    if len(candidates) > 1:
        return {"_ambiguous": candidates}
    return candidates[0]


def _shape_movie_option(rank: int, rel: dict[str, Any], movie_id: int) -> dict[str, Any]:
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
        "movie_id": movie_id,
        "approved": bool(rel.get("approved")),
        "download_allowed": bool(rel.get("downloadAllowed", True)),
        **release_flag_fields(rel),
    }


def search_radarr_releases(
    client: ArrClient,
    *,
    query: str,
    movie_id: int | None,
    config: ReleaseSearchConfig,
) -> dict[str, Any]:
    if client.indexers_enabled_count() == 0:
        return {
            "ok": False,
            "error": {
                "code": "NO_ACTIVE_INDEXERS",
                "message": "Radarr has zero enabled indexers.",
            },
        }
    normalized_query = " ".join(query.split())
    try:
        movie = _movie_match_from_library(client, normalized_query, movie_id)
    except ValueError as exc:
        if str(exc) == "MOVIE_NOT_IN_LIBRARY":
            return {
                "ok": False,
                "error": {
                    "code": "MOVIE_NOT_IN_LIBRARY",
                    "message": "No Radarr match; add the movie to Radarr first.",
                },
            }
        return {"ok": False, "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": str(exc)}}
    if isinstance(movie.get("_ambiguous"), list):
        candidates = movie["_ambiguous"]
        return {
            "ok": False,
            "error": {
                "code": "AMBIGUOUS_MOVIE",
                "message": "Multiple library movies match; pass movie_id.",
                "movie_candidates": [
                    {
                        "id": int_field(row.get("id")),
                        "title": str(row.get("title") or ""),
                        "year": row.get("year"),
                    }
                    for row in candidates
                    if isinstance(row, dict) and int_field(row.get("id")) is not None
                ],
            },
        }
    mid = int_field(movie.get("id")) if isinstance(movie, dict) else None
    if not mid:
        if movie_id is not None:
            return {
                "ok": False,
                "error": {
                    "code": "UNKNOWN_MOVIE_ID",
                    "message": f"No movie with id {movie_id} in Radarr library.",
                },
            }
        return {
            "ok": False,
            "error": {"code": "MOVIE_NOT_IN_LIBRARY", "message": "Not found in Radarr."},
        }
    title = str(movie.get("title") or "Unknown")
    client.post("/command", {"name": "MoviesSearch", "movieIds": [mid]})
    deadline = time.monotonic() + config.search_timeout_s
    merged: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        data = client.get("/release", {"movieId": mid})
        if isinstance(data, list) and any(isinstance(x, dict) for x in data):
            merged = [x for x in data if isinstance(x, dict)]
            break
        time.sleep(config.poll_s)
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
    merged.sort(
        key=lambda row: (
            -(int_field(row.get("seeders")) or 0),
            -(int_field(row.get("leechers")) or 0),
            -(int_field(row.get("size")) or 0),
        )
    )
    limit = min(config.options_limit, 25)
    options = [_shape_movie_option(i, rel, mid) for i, rel in enumerate(merged[:limit], start=1)]
    return {
        "ok": True,
        "type": "movie",
        "movie": {"id": mid, "title": title},
        "query": normalized_query,
        "options": options,
    }


def grab_radarr_release(client: ArrClient, *, movie_id: int, guid: str) -> dict[str, Any]:
    data = client.get("/release", {"movieId": movie_id})
    if not isinstance(data, list):
        return {
            "ok": False,
            "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": "radarr /release is not a list"},
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
                "message": "Release no longer in Radarr cache; run radarr_release_search again.",
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
                "message": f"radarr status {response.status_code}",
            },
        }
    return {"ok": True, "app": "radarr", "command": body}
