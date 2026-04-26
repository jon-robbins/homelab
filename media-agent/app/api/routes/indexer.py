"""POST /indexer-search + POST /indexer-grab endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...models.api import IndexerGrabRequest, IndexerSearchRequest
from ...services.indexer_pipeline import prowlarr_grab, run_indexer_search
from ..auth import err_response
from ..dependencies import AuthContext, AuthDep, get_http
from ..errors import HTTPErrorResponse, translate_upstream_errors
from ..responses import envelope_grab, envelope_indexer

router = APIRouter()


@router.post("/indexer-search")
def indexer_search(
    body: dict = Body(...),
    auth: AuthContext = AuthDep,
) -> JSONResponse:
    request_id = auth.request_id
    try:
        b = IndexerSearchRequest.model_validate(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)
    try:
        with translate_upstream_errors(request_id, upstream="prowlarr"):
            result = run_indexer_search(
                get_http(), auth.settings, b.query, b.search_type, b.limit
            )
    except HTTPErrorResponse as boxed:
        return boxed.response
    return envelope_indexer(result, request_id)


@router.post("/indexer-grab")
def indexer_grab(
    body: dict = Body(...),
    auth: AuthContext = AuthDep,
) -> JSONResponse:
    request_id = auth.request_id
    try:
        g = IndexerGrabRequest.model_validate(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)
    try:
        with translate_upstream_errors(request_id, upstream="prowlarr"):
            result = prowlarr_grab(get_http(), auth.settings, g.release)
    except HTTPErrorResponse as boxed:
        return boxed.response
    return envelope_grab(result, request_id)
