"""Movie download-options + grab pipeline running on top of Radarr /release cache."""
from __future__ import annotations

import time
from typing import Any

import httpx

from ..config import Settings
from ..integrations.radarr import radarr_get as _radarr_get
from ..integrations.radarr import radarr_post_json as _radarr_post_json
from .release_formatting import fold_for_match as _fold
from .release_formatting import human_size
from .release_formatting import indexer_name as _indexer_name
from .release_formatting import int_field as _int_field


def _movie_match_from_library(
    client: httpx.Client, s: Settings, q: str, movie_id: int | None
) -> dict:
    r = _radarr_get(client, s, "api/v3/movie", None)
    r.raise_for_status()
    rows = r.json() if r.is_success else None
    if not isinstance(rows, list):
        raise ValueError("RADARR_BAD")
    if movie_id is not None:
        for row in rows:
            if isinstance(row, dict) and _int_field(row.get("id")) == movie_id:
                return row
        return {}
    f = _fold(q)
    cands = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        t = str(row.get("title") or "")
        ot = str(row.get("originalTitle") or "")
        st = str(row.get("sortTitle") or "")
        ft, fo, fs = _fold(t), _fold(ot), _fold(st)
        if f and (f in ft or f in fo or f in fs or ft in f or fo in f or fs in f):
            cands.append(row)
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise ValueError("MOVIE_NOT_IN_LIBRARY")
    if len(cands) > 1:
        return {"_ambiguous": cands}
    return cands[0]


def run_download_options_movie(
    client: httpx.Client, s: Settings, query: str, movie_id: int | None
) -> dict:
    tq = " ".join(query.split())
    try:
        m = _movie_match_from_library(client, s, tq, movie_id)
    except ValueError as e:
        if str(e) == "MOVIE_NOT_IN_LIBRARY":
            return {
                "ok": False,
                "error": {
                    "code": "MOVIE_NOT_IN_LIBRARY",
                    "message": "No Radarr match; add the movie to Radarr first.",
                },
            }
        return {"ok": False, "error": {"code": "UPSTREAM_BAD_RESPONSE", "message": str(e)}}
    if isinstance(m.get("_ambiguous"), list):
        cands = m["_ambiguous"]
        return {
            "ok": False,
            "error": {
                "code": "AMBIGUOUS_MOVIE",
                "message": "Multiple library movies match; pass movie_id.",
                "movie_candidates": [
                    {
                        "id": _int_field(x.get("id")),
                        "title": str(x.get("title") or ""),
                        "year": x.get("year"),
                    }
                    for x in cands
                    if isinstance(x, dict) and _int_field(x.get("id")) is not None
                ],
            },
        }
    if not m or not isinstance(m, dict) or not _int_field(m.get("id")):
        if movie_id is not None:
            return {
                "ok": False,
                "error": {
                    "code": "UNKNOWN_MOVIE_ID",
                    "message": f"No movie with id {movie_id} in your Radarr library.",
                },
            }
        return {
            "ok": False,
            "error": {"code": "MOVIE_NOT_IN_LIBRARY", "message": "Not found in Radarr."},
        }
    mid = int(m["id"])
    title = str(m.get("title") or "Unknown")
    p = _radarr_post_json(
        client, s, "api/v3/command", {"name": "MoviesSearch", "movieIds": [mid]}
    )
    p.raise_for_status()
    deadline = time.monotonic() + s.download_search_wait_s
    poll = s.download_poll_s
    merged: list[dict] = []
    while time.monotonic() < deadline:
        rr = _radarr_get(client, s, "api/v3/release", {"movieId": mid})
        rr.raise_for_status()
        data = rr.json() if rr.is_success else None
        if isinstance(data, list) and any(isinstance(x, dict) for x in data):
            merged = [x for x in data if isinstance(x, dict)]
            break
        time.sleep(poll)
    if not merged:
        return {
            "ok": False,
            "error": {
                "code": "NO_RELEASES",
                "message": (
                    f"No releases after {int(s.download_search_wait_s)}s; "
                    "check indexers and filters."
                ),
            },
        }
    merged.sort(
        key=lambda d: (
            -int(d.get("seeders") or 0),
            -int(d.get("leechers") or 0),
            -int(d.get("size") or 0),
        )
    )
    lim = s.download_options_limit
    out_opts: list[dict] = []
    for i, rel in enumerate(merged[:lim], start=1):
        sz = int(rel.get("size") or 0)
        out_opts.append(
            {
                "rank": i,
                "title": str(rel.get("title") or "Unknown release"),
                "seeders": int(rel.get("seeders") or 0),
                "leechers": int(rel.get("leechers") or 0),
                "size": sz,
                "size_human": human_size(sz),
                "indexer": _indexer_name(rel),
                "guid": str(rel.get("guid") or ""),
                "movie_id": mid,
                "approved": bool(rel.get("approved")),
                "downloadAllowed": bool(rel.get("downloadAllowed", True)),
                "rejections": rel.get("rejections") or [],
            }
        )
    return {
        "ok": True,
        "type": "movie",
        "movie": {"id": mid, "title": title},
        "search": "MoviesSearch",
        "options": out_opts,
    }


def _strip_internal_release(rel: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rel.items() if not k.startswith("_")}


def grab_radarr(client: httpx.Client, s: Settings, movie_id: int, guid: str) -> dict:
    r = _radarr_get(client, s, "api/v3/release", {"movieId": movie_id})
    r.raise_for_status()
    data = r.json() if r.is_success else None
    if not isinstance(data, list):
        return {
            "ok": False,
            "error": {
                "code": "UPSTREAM_BAD_RESPONSE",
                "message": "radarr /release is not a list",
            },
        }
    selected: dict | None = None
    for rel in data:
        if not isinstance(rel, dict):
            continue
        if str(rel.get("guid") or "") == guid:
            selected = _strip_internal_release(rel)
            break
    if not selected:
        return {
            "ok": False,
            "error": {
                "code": "RELEASE_GONE",
                "message": (
                    "That release is no longer in Radarr’s cache; "
                    "run download-options again."
                ),
            },
        }
    g = _radarr_post_json(client, s, "api/v3/release", selected)
    try:
        body = g.json() if g.content else {}
    except Exception:  # noqa: BLE001
        body = {}
    if g.status_code >= 400:
        return {
            "ok": False,
            "error": {
                "code": "GRAB_FAILED",
                "message": f"radarr status {g.status_code}",
            },
        }
    return {"ok": True, "command": body, "app": "radarr"}


__all__ = [
    "run_download_options_movie",
    "grab_radarr",
]
