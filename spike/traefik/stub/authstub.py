#!/usr/bin/env python3
"""Authentik-like forward-auth stub for the Traefik spike. Not production code.

GET /outpost.goauthentik.io/auth/traefik
    Cookie stub_session=<user>  -> 200 + X-Authentik-* identity headers for that user
    anything else               -> 302 to a login URL + Set-Cookie (what a real outpost does)
GET /outpost.goauthentik.io/<other> -> 200 "outpost" (the callback/start path, never gated)
GET /_last -> JSON of the headers of the most recent auth request (shows what the auth server received)
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USERS = {
    "alice": {"Username": "alice", "Groups": "family", "Email": "alice@example.invalid", "Name": "Alice", "Uid": "u-alice"},
    "bob": {"Username": "bob", "Groups": "family|admins", "Email": "bob@example.invalid", "Name": "Bob", "Uid": "u-bob"},
    # carol: a valid session whose auth response has NO Email header and one extra, unlisted header.
    "carol": {"Username": "carol", "Groups": "family", "Name": "Carol", "Uid": "u-carol", "Extra-Added": "from-auth"},
}
LAST: dict = {}


class Handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/_last":
            return self._reply(200, json.dumps(LAST).encode(), {"Content-Type": "application/json"})
        if self.path.startswith("/outpost.goauthentik.io/auth/"):
            LAST.clear()
            LAST.update({k: v for k, v in self.headers.items()})
            cookies = dict(c.strip().split("=", 1) for c in self.headers.get("Cookie", "").split(";") if "=" in c)
            user = USERS.get(cookies.get("stub_session", ""))
            if user:
                return self._reply(200, b"ok", {f"X-Authentik-{k}": v for k, v in user.items()})
            return self._reply(302, b"", {"Location": "https://auth.example.invalid/login", "Set-Cookie": "stub_nonce=1; Path=/"})
        if self.path == "/outpost.goauthentik.io/echo":  # headers the outpost route actually receives
            return self._reply(200, json.dumps(dict(self.headers.items())).encode(), {"Content-Type": "application/json"})
        if self.path.startswith("/outpost.goauthentik.io/"):
            return self._reply(200, b"outpost")
        return self._reply(404, b"stub: not found")

    def log_message(self, fmt: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
