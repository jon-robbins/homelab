"""GATE-1 (A-5, O-6): prove the forward-auth gate before anything is public.

Checks, in this order: anonymous requests are sent to login; forged identity headers change nothing; a signed-in
family member is who Authentik says, whatever headers they send, and is refused on the admin route; with Authentik
stopped every route fails closed. Probe users exist only for the length of the proof.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Iterator, Sequence
from urllib.parse import urljoin, urlsplit

from ..engine.contract import Context, Result, State
from ..engine.local import Release
from ..engine.ops import edge_compose
from .stack import SERVER, Stack, StackError
from .web import REDIRECTS, Web

WHOAMI, ADMIN = "/.homelab/whoami", "/.homelab/admin"
FLOW = "/api/v3/flows/executor/default-authentication-flow/?query="
FORGED = {
    "X-Authentik-Username": "akadmin",
    "X-Authentik-Groups": "admins",
    "X-Authentik-Email": "forged@example.invalid",
    "Remote-User": "akadmin",
}
# Probes run one at a time, so removing every probe user is both the pre-create sweep and the cleanup.
SWEEP = "from authentik.core.models import User, Group\nUser.objects.filter(username__startswith='homelab-probe-').delete()\n"
FAIL_CLOSED = (502, 503, 504)


class ProofFailed(Exception):
    pass


def _expect(ok: bool, what: str) -> None:
    if not ok:
        raise ProofFailed(what)


@dataclass(frozen=True)
class Gate:
    stack: Stack
    release: Release
    address: tuple[str, int]  # where Caddy listens for the app and auth hosts
    app_url: str
    auth_url: str
    paths: Sequence[str]  # all application routes behind the gate, including admin routes
    admin_paths: Sequence[str] = ()  # the subset a family member must be refused on
    verify_tls: bool = True
    id: str = "GATE-1"
    needs: tuple[str, ...] = field(default=("phase.caddy", "AK-3", "AK-9"), init=False)

    def __post_init__(self) -> None:
        if not self.paths or any(not p.startswith("/") or p.startswith("//") for p in self.paths):
            raise ValueError("supply every gated application path")
        if not set(self.admin_paths) <= set(self.paths):
            raise ValueError("admin paths must also be listed in paths")

    def digest(self, ctx: Context) -> str:
        config = [
            self.release.directory.name,  # already a hash of app.json, edge.json and site.yaml
            ctx.facts.get("authentik.blueprints"),
            self.address,
            self.app_url,
            self.auth_url,
            sorted(self.paths),
            sorted(self.admin_paths),
            self.verify_tls,
        ]
        return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

    def _close_tunnel(self, ctx: Context) -> None:
        if "cloudflared" not in self.release.edge.get("services", {}):
            return
        command = edge_compose(self.release.directory)
        if self.stack.call([*command, "ps", "--status", "running", "-q", "cloudflared"]).strip():
            ctx.layout.set_restart_pending("cloudflared", True)
            self.stack.call([*command, "stop", "cloudflared"])

    def _web(self) -> Web:
        return Web(self.address, self.verify_tls)

    def _sent_to_login(self, web: Web, path: str, headers: dict[str, str] | None = None) -> None:
        reply = web.send("GET", self.app_url + path, headers)
        where = urlsplit(urljoin(self.app_url, reply.location))
        sent = reply.status in REDIRECTS and where.netloc == urlsplit(self.auth_url).netloc
        _expect(sent, f"{path} is not gated: status {reply.status}, redirect to {where.netloc or 'nowhere'}")

    def _anonymous(self) -> None:
        web = self._web()
        for path in (*self.paths, WHOAMI, ADMIN, "/" + secrets.token_hex(6)):
            self._sent_to_login(web, path)
            self._sent_to_login(web, path, FORGED)

    def check(self, ctx: Context) -> State:
        try:
            self._anonymous()
        except (ProofFailed, OSError):
            return State.needed()  # apply reports what exactly is wrong
        return State.done() if ctx.facts.get("gate.digest") == self.digest(ctx) else State.needed()

    def _flow(self, web: Web, payload: dict | None) -> dict:
        url = self.auth_url + FLOW
        body = json.dumps(payload).encode() if payload else None
        reply = web.send("POST" if payload else "GET", url, {"Content-Type": "application/json"}, body)
        if reply.status in REDIRECTS:
            reply, _ = web.follow(urljoin(url, reply.location))
        return reply.json()

    def _sign_in(self, web: Web, user: str, password: str) -> None:
        self._flow(web, None)
        self._flow(web, {"component": "ak-stage-identification", "uid_field": user})
        done = self._flow(web, {"component": "ak-stage-password", "password": password})
        _expect(done.get("component") == "xak-flow-redirect", "probe user could not sign in")

    @contextlib.contextmanager
    def _probe(self, group: str) -> Iterator[Web]:
        name, password = f"homelab-probe-{group}-{secrets.token_hex(6)}", secrets.token_urlsafe(24)
        make = SWEEP + (
            f"u = User.objects.create(username={name!r}, name='probe', path='users')\n"
            f"u.set_password({password!r}); u.save(); u.ak_groups.add(Group.objects.get(name={group!r}))\n"
        )
        try:
            self.stack.shell(make)
            web = self._web()
            self._sign_in(web, name, password)
            yield web
        finally:
            with contextlib.suppress(StackError):  # never hide the proof's own error; the next proof sweeps it
                self.stack.shell(SWEEP)

    def _signed_in(self) -> None:
        with self._probe("family") as web:
            who, _ = web.follow(self.app_url + WHOAMI, headers=FORGED)
            _expect(who.status == 200, "signed-in request was refused")
            user, groups, remote = who.body.decode().split("|")
            _expect(
                user.startswith("homelab-probe-family-") and groups == "family" and not remote,
                "forged headers reached the upstream",
            )
            for path in (ADMIN, *self.admin_paths):
                _expect(
                    web.follow(self.app_url + path)[0].status == 403, f"a family member reached an admin route: {path}"
                )
        with self._probe("admins") as web:
            _expect(web.follow(self.app_url + ADMIN)[0].status == 200, "an admin was refused on the admin route")

    def _open_routes(self, signed_in: Web) -> list[str]:
        """Paths that still answer with Authentik stopped. Failing closed is a 5xx with no identity in the body."""
        paths = (*self.paths, WHOAMI, ADMIN)
        sent = [(p, c.send("GET", self.app_url + p, FORGED)) for c in (signed_in, self._web()) for p in paths]
        return sorted({p for p, reply in sent if reply.status not in FAIL_CLOSED or b"|" in reply.body})

    def _down(self, ctx: Context) -> None:
        with self._probe("family") as web:
            ctx.layout.set_restart_pending(SERVER, True)  # survives a kill, so the engine restarts it
            try:
                self.stack.lifecycle("stop", SERVER)
                opened = self._open_routes(web)
            finally:
                self.stack.lifecycle("start", SERVER)
            back = self._ready()
            if back:
                ctx.layout.set_restart_pending(SERVER, False)
            _expect(not opened, f"{', '.join(opened)}: stayed open with Authentik down")  # reported before an outage
            _expect(back, "Authentik did not come back")

    def _ready(self, seconds: float = 120) -> bool:
        """Healthy, and every route sends visitors to login (the outpost loads its provider a little late)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError, StackError, ProofFailed):
                if self.stack.healthy(SERVER):
                    self._anonymous()
                    return True
            time.sleep(3)
        return False

    def apply(self, ctx: Context) -> Result:
        try:
            self._close_tunnel(ctx)
            if not self._ready():
                self._anonymous()  # a route that is open is reported as one, not as an outage
                raise ProofFailed("Authentik did not come back")
            self._signed_in()
            self._down(ctx)
        except (ProofFailed, StackError, OSError) as error:
            return Result.failure(f"gate proof failed: {error}")
        return Result.success("gate holds", {"gate.digest": self.digest(ctx)})

    def verify(self, ctx: Context) -> Result:
        try:
            self._anonymous()
        except (ProofFailed, OSError) as error:
            self._close_tunnel(ctx)
            return Result.failure(f"gate proof failed: {error}")
        return Result.success()
