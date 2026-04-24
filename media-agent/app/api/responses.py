from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def envelope_download(result: dict, request_id: str) -> JSONResponse:
    if result.get("ok") is True:
        r = {**result, "request_id": request_id, "ok": True}
        return JSONResponse(status_code=200, content=r)
    err = result.get("error") or {}
    code = str(err.get("code") or "DOWNLOAD_ERROR")
    msg = str(err.get("message") or "download request failed")
    status = 400
    if code in ("NO_RELEASES", "UNKNOWN_SERIES_ID", "UNKNOWN_MOVIE_ID"):
        status = 404
    if code in ("UPSTREAM_TIMEOUT", "UPSTREAM_UNAVAILABLE", "UPSTREAM_BAD_RESPONSE"):
        status = 502
    body: dict[str, Any] = {
        "ok": False,
        "request_id": request_id,
        "error": {"code": code, "message": msg},
    }
    for k in ("series_candidates", "movie_candidates"):
        if k in err:
            body["error"][k] = err[k]
    return JSONResponse(status_code=status, content=body)


def envelope_indexer(result: dict, request_id: str) -> JSONResponse:
    if result.get("ok") is True:
        r = {**result, "request_id": request_id, "ok": True}
        return JSONResponse(status_code=200, content=r)
    err = result.get("error") or {}
    code = str(err.get("code") or "INDEXER_ERROR")
    msg = str(err.get("message") or "indexer request failed")
    status = 400
    if code == "PROWLARR_NOT_CONFIGURED":
        status = 503
    if code in (
        "UPSTREAM_TIMEOUT",
        "UPSTREAM_UNAVAILABLE",
        "UPSTREAM_BAD_RESPONSE",
        "GRAB_FAILED",
    ):
        status = 502
    if code == "RELEASE_NOT_CACHED":
        status = 409
    if code == "VALIDATION_ERROR" and "release" in msg:
        status = 400
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "request_id": request_id,
            "error": {"code": code, "message": msg},
        },
    )


def envelope_grab(result: dict, request_id: str) -> JSONResponse:
    if result.get("ok") is True:
        return JSONResponse(status_code=200, content={**result, "request_id": request_id, "ok": True})
    err = result.get("error") or {}
    code = str(err.get("code") or "GRAB_FAILED")
    msg = str(err.get("message") or "grab failed")
    status = 400
    if code in ("GRAB_FAILED", "UPSTREAM_BAD_RESPONSE"):
        status = 502
    if code in ("RELEASE_GONE", "RELEASE_NOT_CACHED"):
        status = 409
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "request_id": request_id,
            "error": {"code": code, "message": msg},
        },
    )


def envelope_action(action_name: str, result: dict, request_id: str) -> JSONResponse:
    if action_name in {"download_options_tv", "download_options_movie"}:
        return envelope_download(result, request_id)
    if action_name == "indexer_search":
        return envelope_indexer(result, request_id)
    if action_name in {"download_grab_tv", "download_grab_movie", "indexer_grab"}:
        return envelope_grab(result, request_id)
    if result.get("ok") is True:
        return JSONResponse(status_code=200, content={**result, "request_id": request_id, "ok": True})
    return JSONResponse(
        status_code=400,
        content={
            "ok": False,
            "request_id": request_id,
            "error": result.get("error") or {
                "code": "ACTION_FAILED",
                "message": "action failed",
            },
        },
    )
