from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import Body, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .actions.action_service import execute_action_payload
from .core.action_catalog import ACTION_DEFINITIONS, ACTION_NAMES
from .api.auth import err_response, new_request_id, verify_bearer
from .api.dependencies import (
    close_http_client,
    get_http,
    init_http_client,
)
from .api.responses import envelope_action, envelope_download, envelope_indexer
from .core.config import get_settings
from .actions.download_options import (
    grab_radarr,
    grab_sonarr,
    run_download_options_movie,
    run_download_options_tv,
)
from .integrations.qbittorrent import (
    completed_download_match_for_action,
    season_only_selection_after_grab,
    try_enable_requested_season_in_existing_torrent,
)
from .actions.lookup import normalize_query, run_lookup
from .core.models import (
    ACTION_CALL_ADAPTER,
    DownloadGrabRequest,
    DownloadOptionsMovieRequest,
    DownloadOptionsTVRequest,
    HealthResponse,
    IndexerGrabRequest,
    IndexerSearchRequest,
    RouterIntentDecision,
    RouterPendingOption,
    RouterRequest,
    RouterSessionState,
    SearchRequestModel,
    SearchSuccessResponse,
)
from .actions.prowlarr_flow import prowlarr_grab, run_indexer_search
from .router.router_contracts import RouterProviderOps
from .router.formatting import format_router_response
from .router.router_orchestrator import (
    build_smoke_gate_payload,
    execute_action,
    hydrate_context,
    parse_intent,
    plan_actions,
    render_response,
)
from .router.parser import classify_intent, parse_router_action
from .router.router_runtime_helpers import (
    _build_pending_options,
    _extract_season_number,
    _selection_to_action_from_option,
)
from .router.router_state import RouterStateStore

# Keep this module focused on route wiring; router policy and execution live in app.router.
_DEBUG_LOG_PATH = "/home/jon/docker/.cursor/debug-f44d4c.log"
_router_state_store: Optional[RouterStateStore] = None
def _debug_log(hypothesis_id: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        line = {
            "sessionId": "f44d4c",
            "runId": "router-runtime-debug",
            "hypothesisId": hypothesis_id,
            "location": "media-agent/app/main.py",
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        p = Path(_DEBUG_LOG_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    # #endregion


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_http_client()
    yield
    close_http_client()


app = FastAPI(
    title="media-agent",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def get_router_state_store() -> RouterStateStore:
    global _router_state_store
    if _router_state_store is None:
        s = get_settings()
        _router_state_store = RouterStateStore(s.router_state_path)
    return _router_state_store


@app.exception_handler(RequestValidationError)
async def validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    rid = new_request_id()
    msg = "; ".join(f"{e['loc']}: {e['msg']}" for e in exc.errors()[:5])[:400]
    return err_response(rid, "VALIDATION_ERROR", msg or "validation failed", 400)


@app.get("/internal/media-agent/v1/health")
def health(authorization: Optional[str] = Header(None)) -> JSONResponse:
    request_id = new_request_id()
    try:
        s = get_settings()
    except RuntimeError as e:
        return err_response(new_request_id(), "INTERNAL_ERROR", str(e), 500)
    if bad := verify_bearer(authorization, s.media_agent_token, request_id):
        return bad
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
    pl: str
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


@app.post("/internal/media-agent/v1/indexer-search")
def indexer_search(
    request: Request,
    authorization: Optional[str] = Header(None),
    body: dict = Body(...),
) -> JSONResponse:
    request_id = new_request_id()
    try:
        s = get_settings()
    except RuntimeError as e:
        return err_response(new_request_id(), "INTERNAL_ERROR", str(e), 500)
    if bad := verify_bearer(authorization, s.media_agent_token, request_id):
        return bad
    try:
        b = IndexerSearchRequest.model_validate(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)
    try:
        result = run_indexer_search(get_http(), s, b.query, b.search_type, b.limit)
    except httpx.TimeoutException:
        return err_response(request_id, "UPSTREAM_TIMEOUT", "prowlarr timed out", 504)
    except httpx.HTTPStatusError as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"prowlarr status {e.response.status_code}"[:200],
            502,
        )
    except (httpx.RequestError, OSError) as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"prowlarr request failed: {e!s}"[:200],
            502,
        )
    except (ValueError, TypeError) as e:
        return err_response(request_id, "UPSTREAM_BAD_RESPONSE", str(e)[:200], 502)
    except Exception as e:  # noqa: BLE001
        return err_response(request_id, "INTERNAL_ERROR", str(e)[:200], 500)
    return envelope_indexer(result, request_id)


@app.post("/internal/media-agent/v1/indexer-grab")
def indexer_grab(
    request: Request,
    authorization: Optional[str] = Header(None),
    body: dict = Body(...),
) -> JSONResponse:
    request_id = new_request_id()
    try:
        s = get_settings()
    except RuntimeError as e:
        return err_response(new_request_id(), "INTERNAL_ERROR", str(e), 500)
    if bad := verify_bearer(authorization, s.media_agent_token, request_id):
        return bad
    try:
        g = IndexerGrabRequest.model_validate(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)
    try:
        result = prowlarr_grab(get_http(), s, g.release)
    except httpx.TimeoutException:
        return err_response(request_id, "UPSTREAM_TIMEOUT", "prowlarr timed out", 504)
    except httpx.HTTPStatusError as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"prowlarr status {e.response.status_code}"[:200],
            502,
        )
    except (httpx.RequestError, OSError) as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"prowlarr request failed: {e!s}"[:200],
            502,
        )
    except (ValueError, TypeError) as e:
        return err_response(request_id, "UPSTREAM_BAD_RESPONSE", str(e)[:200], 502)
    except Exception as e:  # noqa: BLE001
        return err_response(request_id, "INTERNAL_ERROR", str(e)[:200], 500)
    if not result.get("ok"):
        e2 = result.get("error") or {}
        code = str(e2.get("code") or "GRAB_FAILED")
        msg = str(e2.get("message") or "grab failed")
        status = 400
        if code in ("GRAB_FAILED", "UPSTREAM_BAD_RESPONSE"):
            status = 502
        if code == "RELEASE_NOT_CACHED":
            status = 409
        return JSONResponse(
            status_code=status,
            content={
                "ok": False,
                "request_id": request_id,
                "error": {"code": code, "message": msg},
            },
        )
    out = {**result, "request_id": request_id, "ok": True}
    return JSONResponse(status_code=200, content=out)


@app.post("/internal/media-agent/v1/download-options")
def download_options(
    request: Request,
    authorization: Optional[str] = Header(None),
    body: dict = Body(...),
) -> JSONResponse:
    request_id = new_request_id()
    try:
        s = get_settings()
    except RuntimeError as e:
        return err_response(new_request_id(), "INTERNAL_ERROR", str(e), 500)
    if bad := verify_bearer(authorization, s.media_agent_token, request_id):
        return bad
    t = (body or {}).get("type")
    try:
        if t == "movie":
            sm = DownloadOptionsMovieRequest.model_validate(body)
        else:
            sm = DownloadOptionsTVRequest.model_validate(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)
    try:
        if isinstance(sm, DownloadOptionsMovieRequest):
            result = run_download_options_movie(get_http(), s, sm.query, sm.movie_id)
        else:
            result = run_download_options_tv(
                get_http(),
                s,
                sm.query,
                sm.season,
                sm.series_id,
                sm.include_full_series_packs,
            )
    except httpx.TimeoutException:
        return err_response(
            request_id, "UPSTREAM_TIMEOUT", "sonarr or radarr timed out", 504
        )
    except httpx.HTTPStatusError as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"upstream status {e.response.status_code}"[:200],
            502,
        )
    except (httpx.RequestError, OSError) as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"upstream request failed: {e!s}"[:200],
            502,
        )
    except (ValueError, TypeError) as e:
        return err_response(request_id, "UPSTREAM_BAD_RESPONSE", str(e)[:200], 502)
    except Exception as e:  # noqa: BLE001
        return err_response(request_id, "INTERNAL_ERROR", str(e)[:200], 500)
    return envelope_download(result, request_id)


@app.post("/internal/media-agent/v1/download-grab")
def download_grab(
    request: Request,
    authorization: Optional[str] = Header(None),
    body: dict = Body(...),
) -> JSONResponse:
    request_id = new_request_id()
    try:
        s = get_settings()
    except RuntimeError as e:
        return err_response(new_request_id(), "INTERNAL_ERROR", str(e), 500)
    if bad := verify_bearer(authorization, s.media_agent_token, request_id):
        return bad
    try:
        g = DownloadGrabRequest.model_validate(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)
    try:
        if g.type == "tv":
            result = grab_sonarr(get_http(), s, g.episode_id, g.guid)
        else:
            result = grab_radarr(get_http(), s, g.movie_id, g.guid)
    except httpx.TimeoutException:
        return err_response(
            request_id, "UPSTREAM_TIMEOUT", "sonarr or radarr timed out", 504
        )
    except httpx.HTTPStatusError as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"upstream status {e.response.status_code}"[:200],
            502,
        )
    except (httpx.RequestError, OSError) as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"upstream request failed: {e!s}"[:200],
            502,
        )
    except (ValueError, TypeError) as e:
        return err_response(request_id, "UPSTREAM_BAD_RESPONSE", str(e)[:200], 502)
    except Exception as e:  # noqa: BLE001
        return err_response(request_id, "INTERNAL_ERROR", str(e)[:200], 500)
    if not result.get("ok"):
        e2 = result.get("error") or {}
        code = str(e2.get("code") or "GRAB_FAILED")
        msg = str(e2.get("message") or "grab failed")
        status = 400
        if code in ("GRAB_FAILED", "UPSTREAM_BAD_RESPONSE"):
            status = 502
        if code == "RELEASE_GONE":
            status = 409
        return JSONResponse(
            status_code=status,
            content={
                "ok": False,
                "request_id": request_id,
                "error": {"code": code, "message": msg},
            },
        )
    out = {**result, "request_id": request_id, "ok": True}
    return JSONResponse(status_code=200, content=out)


@app.post("/internal/media-agent/v1/search")
def search(
    request: Request,
    authorization: Optional[str] = Header(None),
    body: dict = Body(...),
) -> JSONResponse:
    request_id = new_request_id()
    try:
        s = get_settings()
    except RuntimeError as e:
        return err_response(new_request_id(), "INTERNAL_ERROR", str(e), 500)
    if bad := verify_bearer(authorization, s.media_agent_token, request_id):
        return bad
    try:
        sm = SearchRequestModel.model_validate(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)
    nq = normalize_query(sm.query)
    try:
        results = run_lookup(get_http(), s, sm.type, nq)
    except httpx.TimeoutException:
        return err_response(
            request_id, "UPSTREAM_TIMEOUT", "sonarr or radarr timed out", 504
        )
    except httpx.HTTPStatusError as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"upstream status {e.response.status_code}"[:200],
            502,
        )
    except (httpx.RequestError, OSError) as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"upstream request failed: {e!s}"[:200],
            502,
        )
    except (ValueError, TypeError) as e:
        return err_response(request_id, "UPSTREAM_BAD_RESPONSE", str(e)[:200], 502)
    except Exception as e:  # noqa: BLE001
        return err_response(request_id, "INTERNAL_ERROR", str(e)[:200], 500)

    out = SearchSuccessResponse(
        type=sm.type,
        query=sm.query,
        normalized_query=nq,
        request_id=request_id,
        results=results,
    )
    return JSONResponse(status_code=200, content=out.model_dump())


@app.get("/internal/media-agent/v1/functions")
def list_functions(authorization: Optional[str] = Header(None)) -> JSONResponse:
    request_id = new_request_id()
    try:
        s = get_settings()
    except RuntimeError as e:
        return err_response(new_request_id(), "INTERNAL_ERROR", str(e), 500)
    if bad := verify_bearer(authorization, s.media_agent_token, request_id):
        return bad
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "request_id": request_id,
            "functions": list(ACTION_NAMES),
            "descriptions": {
                action.name: action.description for action in ACTION_DEFINITIONS
            },
            "actions": [action.public_dict() for action in ACTION_DEFINITIONS],
        },
    )


@app.post("/internal/media-agent/v1/action")
def action_dispatch(
    request: Request,
    authorization: Optional[str] = Header(None),
    body: dict = Body(...),
) -> JSONResponse:
    # Single deterministic dispatch point used by both API callers and router orchestration.
    request_id = new_request_id()
    try:
        s = get_settings()
    except RuntimeError as e:
        return err_response(new_request_id(), "INTERNAL_ERROR", str(e), 500)
    if bad := verify_bearer(authorization, s.media_agent_token, request_id):
        return bad
    try:
        call = ACTION_CALL_ADAPTER.validate_python(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)
    try:
        result = execute_action_payload(get_http(), s, body)
    except httpx.TimeoutException:
        return err_response(request_id, "UPSTREAM_TIMEOUT", "upstream timed out", 504)
    except httpx.HTTPStatusError as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"upstream status {e.response.status_code}"[:200],
            502,
        )
    except (httpx.RequestError, OSError) as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"upstream request failed: {e!s}"[:200],
            502,
        )
    except (ValueError, TypeError) as e:
        return err_response(request_id, "UPSTREAM_BAD_RESPONSE", str(e)[:200], 502)
    except Exception as e:  # noqa: BLE001
        return err_response(request_id, "INTERNAL_ERROR", str(e)[:200], 500)
    return envelope_action(str(call.action), result, request_id)


_DEFAULT_ACTION_DISPATCH = action_dispatch


class _MainRouterProviderOps(RouterProviderOps):
    """Adapter that lets router orchestrator call existing route handlers."""

    def __init__(
        self, request: Request, authorization: Optional[str], settings: Any
    ) -> None:
        self._request = request
        self._authorization = authorization
        self._settings = settings

    def classify_intent(
        self, user_message: str, has_session_state: bool
    ) -> RouterIntentDecision:
        return classify_intent(user_message, has_session_state)

    def parse_action(self, user_message: str) -> dict[str, Any]:
        return parse_router_action(get_http(), self._settings, user_message)

    def library_reuse(self, query: str, season: int) -> dict[str, Any] | None:
        return try_enable_requested_season_in_existing_torrent(
            http=get_http(),
            s=self._settings,
            query=query,
            season=season,
        )

    def library_lookup(self, action_payload: dict[str, Any]) -> dict[str, Any]:
        return self.execute_action(action_payload)

    def provider_search(
        self, query: str, season: int | None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        q = query
        if isinstance(season, int):
            q = f"{q} season {season}".strip()
        fallback_action = {
            "action": "indexer_search",
            "query": q,
            "limit": 10,
            "search_type": "search",
        }
        return fallback_action, self.execute_action(fallback_action)

    def provider_grab(self, action_payload: dict[str, Any]) -> dict[str, Any]:
        return self.execute_action(action_payload)

    def execute_action(self, action_payload: dict[str, Any]) -> dict[str, Any]:
        if action_dispatch is not _DEFAULT_ACTION_DISPATCH:
            action_resp = action_dispatch(
                request=self._request,
                authorization=self._authorization,
                body=action_payload,
            )
            return json.loads(action_resp.body.decode("utf-8"))
        return execute_action_payload(get_http(), self._settings, action_payload)

    def post_grab_season_filter(
        self, release: dict[str, Any], season: int
    ) -> dict[str, Any] | None:
        return season_only_selection_after_grab(
            http=get_http(),
            s=self._settings,
            release=release,
            season=season,
        )

    def completed_download_match(self, action_payload: dict[str, Any]) -> str | None:
        return completed_download_match_for_action(
            get_http(), self._settings, action_payload
        )

    def format_response(
        self, action_payload: dict[str, Any], tool_result: dict[str, Any]
    ) -> str:
        return format_router_response(action_payload, tool_result)

    def build_pending_options(
        self, source_action: str, tool_result: dict[str, Any]
    ) -> list[RouterPendingOption]:
        return _build_pending_options(source_action, tool_result)

    def extract_season_number(self, text: str) -> int | None:
        return _extract_season_number(text)

    def selection_to_action(
        self, state: RouterSessionState, option: RouterPendingOption
    ) -> dict[str, Any] | None:
        return _selection_to_action_from_option(state, option)

    def get_session_state(self, session_key: str) -> RouterSessionState | None:
        return get_router_state_store().get(session_key)

    def save_session_state(self, state: RouterSessionState) -> None:
        get_router_state_store().set(state)

    def clear_session_state(self, session_key: str) -> None:
        get_router_state_store().clear(session_key)


@app.post("/internal/media-agent/v1/router")
def router_dispatch(
    request: Request,
    authorization: Optional[str] = Header(None),
    body: dict = Body(...),
) -> JSONResponse:
    # Router endpoint is intentionally thin: parse -> hydrate -> plan -> execute -> render.
    request_id = new_request_id()
    correlation_id = request_id
    try:
        s = get_settings()
    except RuntimeError as e:
        return err_response(new_request_id(), "INTERNAL_ERROR", str(e), 500)
    if bad := verify_bearer(authorization, s.media_agent_token, request_id):
        return bad
    try:
        rb = RouterRequest.model_validate(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)

    ops = _MainRouterProviderOps(
        request=request, authorization=authorization, settings=s
    )
    parsed = parse_intent(
        ops=ops,
        request_id=request_id,
        correlation_id=correlation_id,
        router_request=rb,
    )
    if parsed.intent == "non_media" or parsed.season_prompt_required:
        return render_response(
            request_id=request_id,
            correlation_id=correlation_id,
            parsed=parsed,
        )

    try:
        hydrated = hydrate_context(
            ops=ops,
            request_id=request_id,
            correlation_id=correlation_id,
            router_request=rb,
            parsed=parsed,
        )
    except ValueError as e:
        return err_response(request_id, "VALIDATION_ERROR", str(e)[:200], 400)
    except httpx.TimeoutException:
        return err_response(
            request_id, "UPSTREAM_TIMEOUT", "router parser timed out", 504
        )
    except httpx.HTTPStatusError as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"router parser status {e.response.status_code}"[:200],
            502,
        )
    except (httpx.RequestError, OSError) as e:
        return err_response(
            request_id,
            "UPSTREAM_UNAVAILABLE",
            f"router parser request failed: {e!s}"[:200],
            502,
        )
    except (TypeError, json.JSONDecodeError) as e:
        return err_response(request_id, "UPSTREAM_BAD_RESPONSE", str(e)[:200], 502)
    except Exception as e:  # noqa: BLE001
        return err_response(request_id, "INTERNAL_ERROR", str(e)[:200], 500)

    plan = plan_actions(
        request_id=request_id,
        correlation_id=correlation_id,
        hydrated=hydrated,
    )
    executed = execute_action(
        ops=ops,
        request_id=request_id,
        correlation_id=correlation_id,
        router_request=rb,
        plan=plan,
        router_state_ttl_s=int(s.router_state_ttl_s),
    )
    return render_response(
        request_id=request_id,
        correlation_id=correlation_id,
        parsed=parsed,
        executed=executed,
    )


@app.get("/internal/media-agent/v1/router-smoke-gate")
def router_smoke_gate(
    authorization: Optional[str] = Header(None),
    session_key: Optional[str] = None,
) -> JSONResponse:
    request_id = new_request_id()
    try:
        s = get_settings()
    except RuntimeError as e:
        return err_response(new_request_id(), "INTERNAL_ERROR", str(e), 500)
    if bad := verify_bearer(authorization, s.media_agent_token, request_id):
        return bad
    gate = build_smoke_gate_payload(session_key=session_key or "smoke-cxg-s4")
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "request_id": request_id,
            **gate,
            "verify": {
                "helper": "smoke_gate_verify_season_only(tool_result, season=4)",
                "target": "tool_result.season_selection.status == season_only_applied",
            },
        },
    )
