from __future__ import annotations

from dataclasses import dataclass

import httpx

TMDB_BASE_URL = "https://api.themoviedb.org/3"


@dataclass(frozen=True)
class TmdbMovie:
    id: int
    title: str
    year: int | None


@dataclass(frozen=True)
class TmdbEpisode:
    show_name: str
    season: int
    episode: int
    episode_title: str | None


def _year_from_date(value: str | None) -> int | None:
    if not value or len(value) < 4:
        return None
    try:
        return int(value[:4])
    except ValueError:
        return None


class TmdbClient:
    def __init__(self, api_key: str, *, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.Client(base_url=TMDB_BASE_URL, timeout=10.0)
            self._owns_client = True

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> TmdbClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def search_movie(self, query: str, year: int | None = None) -> TmdbMovie | None:
        params: dict[str, str | int] = {"api_key": self._api_key, "query": query}
        if year is not None:
            params["year"] = year
        resp = self._client.get("/search/movie", params=params)
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if not results:
            return None
        first = results[0]
        movie_id = int(first["id"])
        title = str(first.get("title") or "")
        movie_year = _year_from_date(first.get("release_date")) or year
        return TmdbMovie(id=movie_id, title=title, year=movie_year)

    def search_tv_episode(
        self, query: str, season: int, episode: int
    ) -> TmdbEpisode | None:
        params = {"api_key": self._api_key, "query": query}
        resp = self._client.get("/search/tv", params=params)
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if not results:
            return None
        show = results[0]
        tv_id = int(show["id"])
        show_name = str(show.get("name") or query)
        ep_params = {"api_key": self._api_key}
        ep_resp = self._client.get(
            f"/tv/{tv_id}/season/{season}/episode/{episode}",
            params=ep_params,
        )
        episode_title: str | None
        if ep_resp.status_code == 404:
            episode_title = None
        else:
            ep_resp.raise_for_status()
            ep_data = ep_resp.json()
            raw_title = ep_data.get("name")
            episode_title = str(raw_title) if raw_title else None
        return TmdbEpisode(
            show_name=show_name,
            season=season,
            episode=episode,
            episode_title=episode_title,
        )
