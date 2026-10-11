from __future__ import annotations

from typing import Any

import httpx


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"X-Api-Key": api_key} if api_key else {},
            timeout=float(timeout_seconds),
        )

    def _api_path(self, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        if normalized.startswith("/api/v1/"):
            return normalized
        return f"/api/v1{normalized}"

    def get(self, path: str, query: dict[str, object] | None = None) -> Any:
        resp = self._client.get(self._api_path(path), params=query or {})
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, payload: dict[str, object]) -> Any:
        resp = self._client.post(self._api_path(path), json=payload)
        resp.raise_for_status()
        return resp.json()

    def post_response(self, path: str, payload: dict[str, object]) -> httpx.Response:
        return self._client.post(self._api_path(path), json=payload)

    def search(
        self,
        *,
        query: str,
        search_type: str = "search",
        limit: int = 10,
        indexer_ids: list[int] | None = None,
        tvdb_id: int | None = None,
        tmdb_id: int | None = None,
        imdb_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, object] = {
            "query": query,
            "type": search_type,
            "limit": limit,
        }
        if indexer_ids:
            params["indexerIds"] = ",".join(str(i) for i in indexer_ids)
        if tvdb_id:
            params["tvdbId"] = tvdb_id
        if tmdb_id:
            params["tmdbId"] = tmdb_id
        if imdb_id:
            params["imdbId"] = imdb_id
        payload = self.get("/search", params)
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return []

    def grab_release(self, payload: dict[str, Any]) -> httpx.Response:
        return self.post_response("/search", payload)

    def indexers(self) -> list[dict[str, Any]]:
        payload = self.get("/indexer")
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return []

    def history(self, page_size: int = 1000) -> list[dict[str, Any]]:
        payload = self.get("/history", {"pageSize": page_size})
        records = payload.get("records") if isinstance(payload, dict) else payload
        if isinstance(records, list):
            return [row for row in records if isinstance(row, dict)]
        return []

    def cleanup_backup(self) -> dict[str, Any]:
        return {
            "available": True,
            "indexers": self.indexers(),
            "history": self.history(),
            "history_deletion_supported": False,
        }

    def api_capability_report(self) -> dict[str, Any]:
        return {
            "status": self.get("/system/status"),
            "history_deletion_supported": False,
        }

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ProwlarrClient:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()
