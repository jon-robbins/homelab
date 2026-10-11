from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _safe_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    # pydantic stores the original ValueError object in ctx; stringify it so
    # the 422 response body stays JSON-serializable.
    errors: list[dict[str, object]] = []
    for error in exc.errors():
        item = dict(error)
        if "ctx" in item:
            item["ctx"] = {key: str(value) for key, value in item["ctx"].items()}
        errors.append(item)
    return errors


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _safe_errors(exc)})
