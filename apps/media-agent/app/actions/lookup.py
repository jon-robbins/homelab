"""
Map Sonarr / Radarr lookup API payloads to the stable contract, with optional
library membership checks.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..core.config import Settings
from ..core.models import ExternalIds, ResultItem

_CONTROL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

# Simple TTL cache for library id sets (in-process)
@dataclass
class LibraryCache:
    expires_at: float = 0.0
    tv_tvdbs: set[int] = field(default_factory=set)
    movie_tmdbs: set[int] = field(default_factory=set)
    movie_imdbs: set[str] = field(default_factory=set)


_library_cache = LibraryCache()


def normalize_query(q: str) -> str:
    s = " ".join(q.split())
    s = _CONTROL_RE.sub("", s)
    return s.strip()


def truncate_overview(text: str, max_len: int) -> str:
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def _sonarr_to_item(raw: Any, in_tvdb: set[int], overview_max: int) -> ResultItem:
    ext = raw.get("tvdbId")
    eids = ExternalIds(
        tvdb=int(ext) if ext is not None else None,
        tmdb=raw.get("tmdbId") if raw.get("tmdbId") is not None else None,
        imdb=None,
    )
    year = raw.get("year")
    y = int(year) if year is not None else None
    return ResultItem(
        type="tv",
        title=raw.get("title") or raw.get("cleanTitle") or "Unknown",
        year=y,
        overview=truncate_overview(raw.get("overview") or "", overview_max),
        external_ids=eids,
        in_library=bool(eids.tvdb and eids.tvdb in in_tvdb),
    )


def _radarr_to_item(raw: Any, tmdb_set: set[int], imdb_set: set[str], overview_max: int) -> ResultItem:
    imdb = raw.get("imdbId")
    imdb_s = str(imdb).strip() if imdb is not None else None
    if imdb_s:
        imdb_s = imdb_s if imdb_s.startswith("tt") else f"tt{imdb_s.lstrip('t')}"
    tid = raw.get("tmdbId")
    tmdb = int(tid) if tid is not None else None
    eids = ExternalIds(tvdb=None, tmdb=tmdb, imdb=imdb_s)
    year = raw.get("year")
    y = int(year) if year is not None else None
    in_lib = (tmdb is not None and tmdb in tmdb_set) or (
        imdb_s is not None and imdb_s.lower() in imdb_set
    )
    return ResultItem(
        type="movie",
        title=raw.get("title") or "Unknown",
        year=y,
        overview=truncate_overview(raw.get("overview") or "", overview_max),
        external_ids=eids,
        in_library=in_lib,
    )


def refresh_library_ids(client: httpx.Client, s: Settings) -> None:
    now = time.time()
    if now < _library_cache.expires_at and (
        _library_cache.expires_at > 0
    ):
        return
    tvdbs: set[int] = set()
    tmdbs: set[int] = set()
    imdbs: set[str] = set()
    h = {"X-Api-Key": s.sonarr_api_key}
    try:
        r1 = client.get(
            f"{s.sonarr_base}/api/v3/series",
            headers=h,
            timeout=s.upstream_timeout_s,
        )
        r1.raise_for_status()
        for row in r1.json() or []:
            tid = row.get("tvdbId")
            if tid is not None:
                tvdbs.add(int(tid))
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError):
        pass
    h2 = {"X-Api-Key": s.radarr_api_key}
    try:
        r2 = client.get(
            f"{s.radarr_base}/api/v3/movie",
            headers=h2,
            timeout=s.upstream_timeout_s,
        )
        r2.raise_for_status()
        for row in r2.json() or []:
            tid = row.get("tmdbId")
            if tid is not None:
                tmdbs.add(int(tid))
            im = row.get("imdbId")
            if im is not None:
                imdbs.add(str(im).strip().lower())
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError):
        pass
    _library_cache.tv_tvdbs = tvdbs
    _library_cache.movie_tmdbs = tmdbs
    _library_cache.movie_imdbs = imdbs
    _library_cache.expires_at = now + s.library_cache_ttl_s


def run_lookup(
    client: httpx.Client,
    settings: Settings,
    media_type: str,
    term: str,
) -> list[ResultItem]:
    refresh_library_ids(client, settings)
    in_tv = _library_cache.tv_tvdbs
    mt = _library_cache.movie_tmdbs
    mi = _library_cache.movie_imdbs
    lim = settings.result_limit
    omax = settings.overview_max_chars

    if media_type == "tv":
        r = client.get(
            f"{settings.sonarr_base}/api/v3/series/lookup",
            params={"term": term},
            headers={"X-Api-Key": settings.sonarr_api_key},
            timeout=settings.upstream_timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise ValueError("sonarr lookup response is not a list")
        out: list[ResultItem] = []
        for raw in data[: lim * 2]:  # extra for dedupe
            if not isinstance(raw, dict):
                continue
            try:
                out.append(_sonarr_to_item(raw, in_tv, omax))
            except Exception:
                continue
        return _dedupe_results(out)[:lim]

    r = client.get(
        f"{settings.radarr_base}/api/v3/movie/lookup",
        params={"term": term},
        headers={"X-Api-Key": settings.radarr_api_key},
        timeout=settings.upstream_timeout_s,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError("radarr lookup response is not a list")
    out = []
    for raw in data[: lim * 2]:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(_radarr_to_item(raw, mt, mi, omax))
        except Exception:
            continue
    return _dedupe_results(out)[:lim]


def _dedupe_results(items: list[ResultItem]) -> list[ResultItem]:
    seen: set = set()
    unique: list[ResultItem] = []
    for it in items:
        k = (it.type, it.title, it.year, it.external_ids.tvdb, it.external_ids.tmdb, it.external_ids.imdb)
        if k in seen:
            continue
        seen.add(k)
        unique.append(it)
    return unique
