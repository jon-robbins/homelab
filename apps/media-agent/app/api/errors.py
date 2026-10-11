"""Single mapping of upstream/internal exceptions to ``JSONResponse``.

Routes wrap their work in ``translate_upstream_errors(request_id, upstream=...)``
and let upstream exceptions surface; the context manager rewrites them into
the project's standard ``ErrorResponse`` envelope. The ``upstream`` label is
threaded through so each route keeps its own message wording (e.g.
``"prowlarr timed out"`` vs ``"sonarr or radarr timed out"``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .auth import err_response, new_request_id


class HTTPErrorResponse(Exception):
    """Wraps a JSONResponse so it can be raised out of a context manager."""

    def __init__(self, response: JSONResponse) -> None:
        self.response = response


@contextmanager
def translate_upstream_errors(
    request_id: str,
    *,
    upstream: str = "upstream",
    timeout_label: str | None = None,
) -> Iterator[None]:
    """Translate the usual upstream/parsing/internal exceptions to envelopes.

    ``upstream`` is used for the HTTP-status and request-error messages.
    ``timeout_label`` (default == ``upstream``) is used in the timeout
    message — split because today's ``/download-*`` and ``/search`` routes
    say ``"sonarr or radarr timed out"`` for timeouts but ``"upstream
    status N"`` / ``"upstream request failed: ..."`` for other errors.
    """
    timeout_text = timeout_label if timeout_label is not None else upstream
    try:
        yield
    except HTTPErrorResponse:
        raise
    except httpx.TimeoutException as exc:
        raise HTTPErrorResponse(
            err_response(
                request_id, "UPSTREAM_TIMEOUT", f"{timeout_text} timed out", 504
            )
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPErrorResponse(
            err_response(
                request_id,
                "UPSTREAM_UNAVAILABLE",
                f"{upstream} status {exc.response.status_code}"[:200],
                502,
            )
        ) from exc
    except (httpx.RequestError, OSError) as exc:
        raise HTTPErrorResponse(
            err_response(
                request_id,
                "UPSTREAM_UNAVAILABLE",
                f"{upstream} request failed: {exc!s}"[:200],
                502,
            )
        ) from exc
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPErrorResponse(
            err_response(request_id, "UPSTREAM_BAD_RESPONSE", str(exc)[:200], 502)
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPErrorResponse(
            err_response(request_id, "INTERNAL_ERROR", str(exc)[:200], 500)
        ) from exc


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the project's standard validation handler to the FastAPI app."""

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(  # noqa: ARG001  — FastAPI signature
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        rid = new_request_id()
        msg = "; ".join(f"{e['loc']}: {e['msg']}" for e in exc.errors()[:5])[:400]
        return err_response(rid, "VALIDATION_ERROR", msg or "validation failed", 400)

    @app.exception_handler(HTTPErrorResponse)
    async def _http_error_handler(  # noqa: ARG001  — FastAPI signature
        request: Request, exc: HTTPErrorResponse
    ) -> JSONResponse:
        return exc.response


__all__ = [
    "HTTPErrorResponse",
    "translate_upstream_errors",
    "register_exception_handlers",
]
