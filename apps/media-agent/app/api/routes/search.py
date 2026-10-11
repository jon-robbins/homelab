"""POST /search endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...models.api import SearchRequestModel, SearchSuccessResponse
from ...services.lookup import normalize_query, run_lookup
from ..auth import err_response
from ..dependencies import AuthContext, AuthDep, get_http
from ..errors import HTTPErrorResponse, translate_upstream_errors

router = APIRouter()


@router.post("/search")
def search(
    body: dict = Body(...),
    auth: AuthContext = AuthDep,
) -> JSONResponse:
    request_id = auth.request_id
    try:
        sm = SearchRequestModel.model_validate(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)
    nq = normalize_query(sm.query)
    try:
        with translate_upstream_errors(
            request_id, upstream="upstream", timeout_label="sonarr or radarr"
        ):
            results = run_lookup(get_http(), auth.settings, sm.type, nq)
    except HTTPErrorResponse as boxed:
        return boxed.response
    out = SearchSuccessResponse(
        type=sm.type,
        query=sm.query,
        normalized_query=nq,
        request_id=request_id,
        results=results,
    )
    return JSONResponse(status_code=200, content=out.model_dump())
