"""A small HTTPS client for gate proofs: it dials one fixed address whatever the URL says, keeps cookies per host,
and never follows a redirect by itself, so a proof sees every hop."""

from __future__ import annotations

import http.client
import json
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

REDIRECTS = (301, 302, 303, 307)


@dataclass(frozen=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def location(self) -> str:
        return self.headers.get("location", "")

    def json(self) -> dict:
        return json.loads(self.body or b"{}")


class _Connection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: tuple[str, int], context: ssl.SSLContext):
        super().__init__(host, port, context=context, timeout=15)
        self._address = address

    def connect(self) -> None:
        self.sock = self._context.wrap_socket(
            socket.create_connection(self._address, self.timeout), server_hostname=self.host
        )


class Web:
    def __init__(self, address: tuple[str, int], verify: bool = True):
        self.address = address
        self.context = ssl.create_default_context()
        if not verify:  # only for a throwaway test certificate
            self.context.check_hostname = False
            self.context.verify_mode = ssl.CERT_NONE
        self.cookies: dict[str, dict[str, str]] = {}

    def send(self, method: str, url: str, headers: dict[str, str] | None = None, body: bytes | None = None) -> Reply:
        parts = urlsplit(url)
        sent = {"Accept": "application/json", **(headers or {})}
        if jar := self.cookies.get(parts.hostname or ""):
            sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
        connection = _Connection(parts.hostname or "", parts.port or 443, self.address, self.context)
        try:
            connection.request(method, parts.path + (f"?{parts.query}" if parts.query else ""), body, sent)
            response = connection.getresponse()
            reply = Reply(response.status, {k.lower(): v for k, v in response.getheaders()}, response.read())
            for cookie in response.headers.get_all("Set-Cookie") or ():
                name, _, value = cookie.split(";")[0].partition("=")
                self.cookies.setdefault(parts.hostname or "", {})[name] = value
            return reply
        finally:
            connection.close()

    def follow(self, url: str, hops: int = 8, **kwargs) -> tuple[Reply, str]:
        """GET, following redirects; returns the final reply and the URL it came from."""
        for _ in range(hops):
            reply = self.send("GET", url, **kwargs)
            if reply.status not in REDIRECTS or not reply.location:
                return reply, url
            url = urljoin(url, reply.location)
        raise RuntimeError(f"more than {hops} redirects")
