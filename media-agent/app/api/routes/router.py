"""POST /router + GET /router-smoke-gate endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...models.router import RouterRequest
from ...router.orchestrator import RouterContext, dispatch
from ...router.smoke import build_smoke_gate_payload
from ..auth import err_response
from ..dependencies import (
    AuthContext,
    AuthDep,
    get_http,
    get_router_state_store,
)
from ..errors import HTTPErrorResponse, translate_upstream_errors

router = APIRouter()

_logger = logging.getLogger("app.router")


@router.post("/router")
def router_dispatch(
    body: dict = Body(...),
    auth: AuthContext = AuthDep,
) -> JSONResponse:
    request_id = auth.request_id
    try:
        rb = RouterRequest.model_validate(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)

    ctx = RouterContext(
        http=get_http(),
        settings=auth.settings,
        state_store=get_router_state_store(),
        logger=_logger,
    )
    try:
        with translate_upstream_errors(request_id, upstream="router parser"):
            try:
                body_out = dispatch(ctx, rb, request_id)
            except ValueError as exc:
                return err_response(
                    request_id, "VALIDATION_ERROR", str(exc)[:200], 400
                )
    except HTTPErrorResponse as boxed:
        return boxed.response
    return JSONResponse(status_code=200, content=body_out)


@router.get("/router-smoke-gate")
def router_smoke_gate(
    session_key: str | None = None,
    auth: AuthContext = AuthDep,
) -> JSONResponse:
    gate = build_smoke_gate_payload(session_key=session_key or "smoke-cxg-s4")
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "request_id": auth.request_id,
            **gate,
            "verify": {
                "helper": "smoke_gate_verify_season_only(tool_result, season=4)",
                "target": "tool_result.season_selection.status == season_only_applied",
            },
        },
    )
