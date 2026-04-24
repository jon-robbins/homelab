from __future__ import annotations

from typing import Optional

import httpx

from ..core.config import get_settings
from ..router.router_state import RouterStateStore

_http: Optional[httpx.Client] = None
_router_state_store: Optional[RouterStateStore] = None


def init_http_client() -> None:
    global _http
    _http = httpx.Client(verify=True, follow_redirects=True, http2=False)


def close_http_client() -> None:
    global _http
    if _http is not None:
        _http.close()
        _http = None


def get_http() -> httpx.Client:
    if _http is None:
        raise RuntimeError("http client not initialized")
    return _http


def get_router_state_store() -> RouterStateStore:
    global _router_state_store
    if _router_state_store is None:
        s = get_settings()
        _router_state_store = RouterStateStore(s.router_state_path)
    return _router_state_store


def reset_router_state_store() -> None:
    global _router_state_store
    _router_state_store = None
