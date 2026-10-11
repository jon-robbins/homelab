from __future__ import annotations

from typing import Any

import httpx

from homelab_workers.shared.seerr_reconciler_models import (
    NormalizedSeerrRequest,
    StableIds,
)


class SeerrClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"X-Api-Key": api_key} if api_key else {},
            timeout=float(timeout_seconds),
        )

    def requests(
        self,
        *,
        take: int = 100,
        skip: int = 0,
        filter_status: str = "approved",
    ) -> list[dict[str, Any]]:
        resp = self._client.get(
            "/api/v1/request",
            params={"take": take, "skip": skip, "filter": filter_status},
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("results") if isinstance(payload, dict) else payload
        return [row for row in rows or [] if isinstance(row, dict)]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SeerrClient:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()


def _int_or_none(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _year_from(value: object) -> int | None:
    if not isinstance(value, str) or len(value) < 4:
        return None
    return _int_or_none(value[:4])


def _media(row: dict[str, Any]) -> dict[str, Any]:
    media = row.get("media") or row.get("mediaInfo")
    return media if isinstance(media, dict) else {}


def _media_type(row: dict[str, Any], media: dict[str, Any]) -> str:
    raw = str(row.get("type") or media.get("mediaType") or media.get("type") or "").lower()
    if raw == "movie":
        return "movie"
    if raw in {"tv", "series"}:
        return "series"
    return "unknown"


def _seasons(row: dict[str, Any]) -> tuple[int, ...]:
    raw = row.get("seasons")
    seasons: set[int] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                season = _int_or_none(item.get("seasonNumber"))
            else:
                season = _int_or_none(item)
            if season is not None:
                seasons.add(season)
    return tuple(sorted(seasons))


def _status(value: object) -> str:
    mapping = {1: "pending", 2: "approved", 3: "declined", 4: "available", 5: "partial"}
    if isinstance(value, str) and value:
        return value.lower()
    return mapping.get(_int_or_none(value) or 0, str(value or "unknown"))


def normalize_seerr_request(row: dict[str, Any]) -> NormalizedSeerrRequest | None:
    media = _media(row)
    media_type = _media_type(row, media)
    if media_type not in {"movie", "series"}:
        return None
    request_id = _int_or_none(row.get("id"))
    title = str(media.get("title") or media.get("name") or row.get("title") or "").strip()
    if request_id is None or not title:
        return None
    date_key = "releaseDate" if media_type == "movie" else "firstAirDate"
    return NormalizedSeerrRequest(
        request_id=request_id,
        media_type=media_type,  # type: ignore[arg-type]
        title=title,
        year=_year_from(media.get(date_key)),
        seasons=() if media_type == "movie" else _seasons(row),
        stable_ids=StableIds(
            tmdb_id=_int_or_none(media.get("tmdbId")),
            tvdb_id=_int_or_none(media.get("tvdbId")),
            imdb_id=str(media.get("imdbId") or "").strip() or None,
        ),
        arr_id=_int_or_none(
            media.get("externalServiceId") or media.get("serviceId") or media.get("radarrId") or media.get("sonarrId")
        ),
        status=_status(row.get("status")),
        requested_at=str(row.get("createdAt") or row.get("requestedAt") or "") or None,
    )
