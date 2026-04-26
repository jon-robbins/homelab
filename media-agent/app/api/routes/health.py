"""GET /health endpoint."""

from __future__ import annotations

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...models.api import HealthResponse
from ..dependencies import AuthContext, AuthDep, get_http

router = APIRouter()


@router.get("/health")
def health(auth: AuthContext = AuthDep) -> JSONResponse:
    s = auth.settings
    son = "down"
    rad = "down"
    try:
        r1 = get_http().get(
            f"{s.sonarr_base}/api/v3/system/status",
            headers={"X-Api-Key": s.sonarr_api_key},
            timeout=s.upstream_timeout_s,
        )
        son = "ok" if r1.is_success else "degraded"
    except (httpx.TimeoutException, httpx.RequestError, OSError):
        son = "down"
    try:
        r2 = get_http().get(
            f"{s.radarr_base}/api/v3/system/status",
            headers={"X-Api-Key": s.radarr_api_key},
            timeout=s.upstream_timeout_s,
        )
        rad = "ok" if r2.is_success else "degraded"
    except (httpx.TimeoutException, httpx.RequestError, OSError):
        rad = "down"
    if s.prowlarr_configured:
        pl = "down"
        try:
            r3 = get_http().get(
                f"{s.prowlarr_base}/api/v1/system/status",
                headers={"X-Api-Key": s.prowlarr_api_key},
                timeout=s.upstream_timeout_s,
            )
            pl = "ok" if r3.is_success else "degraded"
        except (httpx.TimeoutException, httpx.RequestError, OSError):
            pl = "down"
    else:
        pl = "n/a"
    body = HealthResponse(
        ok=son in ("ok", "degraded") and rad in ("ok", "degraded"),
        service="media-agent",
        sonarr=son,
        radarr=rad,
        prowlarr=pl,
    )
    return JSONResponse(status_code=200, content=body.model_dump())
