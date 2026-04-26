"""POST /download-options + POST /download-grab endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ...models.api import (
    DownloadGrabRequest,
    DownloadOptionsMovieRequest,
    DownloadOptionsTVRequest,
)
from ...services.radarr_release_pipeline import grab_radarr, run_download_options_movie
from ...services.sonarr_release_pipeline import grab_sonarr, run_download_options_tv
from ..auth import err_response
from ..dependencies import AuthContext, AuthDep, get_http
from ..errors import HTTPErrorResponse, translate_upstream_errors
from ..responses import envelope_download, envelope_grab

router = APIRouter()


@router.post("/download-options")
def download_options(
    body: dict = Body(...),
    auth: AuthContext = AuthDep,
) -> JSONResponse:
    request_id = auth.request_id
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
        with translate_upstream_errors(
            request_id, upstream="upstream", timeout_label="sonarr or radarr"
        ):
            if isinstance(sm, DownloadOptionsMovieRequest):
                result = run_download_options_movie(
                    get_http(), auth.settings, sm.query, sm.movie_id
                )
            else:
                result = run_download_options_tv(
                    get_http(),
                    auth.settings,
                    sm.query,
                    sm.season,
                    sm.series_id,
                    sm.include_full_series_packs,
                )
    except HTTPErrorResponse as boxed:
        return boxed.response
    return envelope_download(result, request_id)


@router.post("/download-grab")
def download_grab(
    body: dict = Body(...),
    auth: AuthContext = AuthDep,
) -> JSONResponse:
    request_id = auth.request_id
    try:
        g = DownloadGrabRequest.model_validate(body)
    except ValidationError as e:
        m = "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])[:400]
        return err_response(request_id, "VALIDATION_ERROR", m, 400)
    try:
        with translate_upstream_errors(
            request_id, upstream="upstream", timeout_label="sonarr or radarr"
        ):
            if g.type == "tv":
                result = grab_sonarr(get_http(), auth.settings, g.episode_id, g.guid)
            else:
                result = grab_radarr(get_http(), auth.settings, g.movie_id, g.guid)
    except HTTPErrorResponse as boxed:
        return boxed.response
    return envelope_grab(result, request_id)
