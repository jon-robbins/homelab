from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from ..config import Settings


def radarr_get(
    client: httpx.Client, s: Settings, path: str, params: dict | None = None
) -> httpx.Response:
    url = urljoin(f"{s.radarr_base}/", path.lstrip("/"))
    return client.get(
        url,
        params=params,
        headers={"X-Api-Key": s.radarr_api_key},
        timeout=s.upstream_timeout_s,
    )


def radarr_post_json(
    client: httpx.Client, s: Settings, path: str, body: Any
) -> httpx.Response:
    url = urljoin(f"{s.radarr_base}/", path.lstrip("/"))
    return client.post(
        url,
        json=body,
        headers={"X-Api-Key": s.radarr_api_key, "Content-Type": "application/json"},
        timeout=s.upstream_timeout_s,
    )
