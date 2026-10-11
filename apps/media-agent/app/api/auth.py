from __future__ import annotations

import uuid
from secrets import compare_digest

from fastapi.responses import JSONResponse

from ..models import ErrorBody, ErrorResponse


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


def err_response(
    request_id: str,
    code: str,
    message: str,
    status_code: int,
) -> JSONResponse:
    body = ErrorResponse(
        request_id=request_id,
        error=ErrorBody(code=code, message=message),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def verify_bearer(
    authorization: str | None, expected: str, request_id: str
) -> JSONResponse | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return err_response(
            request_id, "UNAUTHORIZED", "missing or invalid Authorization header", 401
        )
    got = authorization.split(" ", 1)[1].strip()
    if not got or not compare_digest(got, expected):
        return err_response(request_id, "UNAUTHORIZED", "invalid token", 401)
    return None
