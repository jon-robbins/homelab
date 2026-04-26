"""GET /functions + POST /action endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...actions import registry
from ...actions.registry import ActionContext
from ...models.actions import ACTION_CALL_ADAPTER
from ..auth import err_response
from ..dependencies import AuthContext, AuthDep, get_http
from ..errors import HTTPErrorResponse, translate_upstream_errors
from ..responses import envelope_action

router = APIRouter()


@router.get("/functions")
def list_functions(auth: AuthContext = AuthDep) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "request_id": auth.request_id,
            "functions": list(registry.all_names()),
            "descriptions": {h.name: h.description for h in registry.all_handlers()},
            "actions": registry.all_definitions(),
        },
    )


@router.post("/action")
def action_dispatch(
    body: dict = Body(...),
    auth: AuthContext = AuthDep,
) -> JSONResponse:
    request_id = auth.request_id
    try:
        call = ACTION_CALL_ADAPTER.validate_python(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)
    try:
        with translate_upstream_errors(request_id, upstream="upstream"):
            result = registry.dispatch(
                ActionContext(http=get_http(), settings=auth.settings), body
            )
    except HTTPErrorResponse as boxed:
        return boxed.response
    return envelope_action(str(call.action), result, request_id)
