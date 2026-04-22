from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx


def read_api_key_from_config_xml(path: str | Path) -> str:
    cfg = Path(path)
    if not cfg.exists():
        return ""
    text = cfg.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"<ApiKey>([^<]+)</ApiKey>", text)
    return match.group(1).strip() if match else ""


class ArrClient:
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
        if normalized.startswith("/api/v3/"):
            return normalized
        return f"/api/v3{normalized}"

    def _params(self, query: dict[str, object] | None = None) -> dict[str, object]:
        params = dict(query or {})
        if self.api_key:
            params.setdefault("apikey", self.api_key)
        return params

    def get(self, path: str, query: dict[str, object] | None = None) -> Any:
        resp = self._client.get(self._api_path(path), params=self._params(query))
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, payload: dict[str, object]) -> Any:
        resp = self._client.post(self._api_path(path), params=self._params(), json=payload)
        resp.raise_for_status()
        return resp.json()

    def delete(self, path: str, query: dict[str, object] | None = None) -> None:
        resp = self._client.delete(self._api_path(path), params=self._params(query))
        resp.raise_for_status()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ArrClient:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()
