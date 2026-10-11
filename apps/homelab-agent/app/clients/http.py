from __future__ import annotations

from typing import Any

import httpx

# Transient transport failures (connection resets, timeouts) are retried once
# before the error surfaces to the agent loop.
TRANSIENT_RETRIES = 1


def post_with_retry(client: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
    attempts = TRANSIENT_RETRIES + 1
    for attempt in range(attempts):
        try:
            return client.post(url, **kwargs)
        except httpx.TransportError:
            if attempt == attempts - 1:
                raise
    raise AssertionError("unreachable")
