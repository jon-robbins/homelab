from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerAuthMiddleware:
    """Pure ASGI bearer auth (BaseHTTPMiddleware breaks SSE/streaming)."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/health":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        expected = f"Bearer {self._token}"
        if not self._token or auth != expected:
            response = JSONResponse(
                {"ok": False, "error": {"code": "UNAUTHORIZED", "message": "invalid token"}},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
