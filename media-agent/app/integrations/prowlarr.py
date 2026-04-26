from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from ..config import Settings


def prowlarr_get(
    client: httpx.Client, s: Settings, path: str, params: dict | None = None
) -> httpx.Response:
    url = urljoin(f"{s.prowlarr_base}/", path.lstrip("/"))
    return client.get(
        url,
        params=params,
        headers={"X-Api-Key": s.prowlarr_api_key},
        timeout=s.prowlarr_search_timeout_s,
    )


def prowlarr_post_json(
    client: httpx.Client, s: Settings, path: str, body: Any
) -> httpx.Response:
    url = urljoin(f"{s.prowlarr_base}/", path.lstrip("/"))
    return client.post(
        url,
        json=body,
        headers={
            "X-Api-Key": s.prowlarr_api_key,
            "Content-Type": "application/json",
        },
        timeout=s.prowlarr_search_timeout_s,
    )
