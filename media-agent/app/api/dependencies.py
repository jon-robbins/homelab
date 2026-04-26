from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import Depends, Header
from fastapi.responses import JSONResponse

from ..config import Settings, get_settings
from ..router.session import RouterStateStore
from .auth import new_request_id, verify_bearer
from .errors import HTTPErrorResponse

_http: httpx.Client | None = None
_router_state_store: RouterStateStore | None = None


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


@dataclass(slots=True, frozen=True)
class AuthContext:
    request_id: str
    settings: Settings


def authenticated_request(
    authorization: str | None = Header(None),
) -> AuthContext:
    """Shared route dependency: generate request id, load settings, verify bearer.

    Failures (settings load or auth) are raised as ``HTTPErrorResponse``;
    each route's outer try/except converts them into the JSON response so we
    keep the existing status codes and bodies.
    """
    request_id = new_request_id()
    try:
        settings = get_settings()
    except RuntimeError as exc:
        raise HTTPErrorResponse(
            JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "request_id": new_request_id(),
                    "error": {"code": "INTERNAL_ERROR", "message": str(exc)},
                },
            )
        ) from exc
    bad = verify_bearer(authorization, settings.media_agent_token, request_id)
    if bad is not None:
        raise HTTPErrorResponse(bad)
    return AuthContext(request_id=request_id, settings=settings)


AuthDep = Depends(authenticated_request)
