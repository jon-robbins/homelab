"""The gate proof must fail when the gate is wrong, and must always bring Authentik back."""

import contextlib
from dataclasses import replace

from setup.engine.local import Release

import pytest

from setup.engine.contract import Status
from setup.wiring.gate import ADMIN, WHOAMI, Gate, ProofFailed
from setup.wiring.web import Reply

APP, AUTH = "https://app.example.test", "https://auth.example.test"


def reply(status, body=b"", location=""):
    return Reply(status, {"location": location} if location else {}, body)


class FakeWeb:
    """Answers by path. `routes` maps a path to a Reply; anything else is sent to login like a correct gate."""

    def __init__(self, routes=None):
        self.routes = routes or {}

    def send(self, method, url, headers=None, body=None):
        path = url.removeprefix(APP)
        return self.routes.get(path, reply(302, location=f"{AUTH}/application/o/authorize/?x=1"))

    def follow(self, url, hops=8, headers=None):
        return self.send("GET", url, headers), url


class FakeStack:
    def __init__(self):
        self.events = []

    def lifecycle(self, verb, service):
        self.events.append((verb, service))

    def shell(self, code):
        self.events.append(("shell", None))
        return ""


@pytest.fixture
def gate(monkeypatch, ctx):
    stack, web = FakeStack(), FakeWeb()
    monkeypatch.setattr(Gate, "_web", lambda self: web)
    release = Release.from_models(ctx.layout, {}, {}, {})
    made = Gate(stack, release, ("127.0.0.1", 443), APP, AUTH, paths=("/sonarr", "/radarr"))
    return made, stack, web


def test_a_gate_that_sends_everything_to_login_passes(gate):
    made, _, _ = gate
    made._anonymous()


def test_an_open_route_is_found_by_name(gate):
    made, _, web = gate
    web.routes["/radarr"] = reply(200, b"<html>Radarr</html>")
    with pytest.raises(ProofFailed, match="/radarr is not gated: status 200"):
        made._anonymous()


def test_a_redirect_to_some_other_host_is_not_a_gate(gate):
    made, _, web = gate
    web.routes[WHOAMI] = reply(302, location="https://elsewhere.example.test/login")
    with pytest.raises(ProofFailed, match="not gated"):
        made._anonymous()


def test_an_unknown_path_must_be_gated_too(gate):
    made, _, web = gate

    class OpenOnUnknown(FakeWeb):
        def send(self, method, url, headers=None, body=None):
            known = {"/sonarr", "/radarr", WHOAMI, ADMIN}
            return super().send(method, url, headers) if url.removeprefix(APP) in known else reply(404, b"Not Found")

    web.send = OpenOnUnknown().send
    with pytest.raises(ProofFailed, match="status 404"):
        made._anonymous()


def test_check_needs_a_proof_for_this_exact_configuration(gate, ctx):
    made, _, _ = gate
    assert made.check(ctx).status is Status.NEEDED
    ctx.facts["gate.digest"] = made.digest(ctx)
    assert made.check(ctx).status is Status.DONE
    changed = replace(made, paths=("/sonarr", "/radarr", "/bazarr"))
    assert changed.check(ctx).status is Status.NEEDED  # a new route has not been proved


def test_forged_headers_that_reach_the_upstream_fail_the_proof(gate, monkeypatch):
    made, _, _ = gate
    leaky = FakeWeb({WHOAMI: reply(200, b"akadmin|admins|akadmin")})
    monkeypatch.setattr(Gate, "_probe", lambda self, group: contextlib.nullcontext(leaky))
    with pytest.raises(ProofFailed, match="forged headers reached"):
        made._signed_in()


def test_a_family_member_on_the_admin_route_fails_the_proof(gate, monkeypatch):
    made, _, _ = gate
    web = FakeWeb({WHOAMI: reply(200, b"homelab-probe-family-abcdef|family|"), ADMIN: reply(200, b"admin")})
    monkeypatch.setattr(Gate, "_probe", lambda self, group: contextlib.nullcontext(web))
    with pytest.raises(ProofFailed, match="family member reached an admin route"):
        made._signed_in()


def test_authentik_is_restarted_even_when_a_route_stays_open_while_it_is_down(gate, monkeypatch, ctx):
    made, stack, _ = gate
    open_web = FakeWeb({WHOAMI: reply(200, b"x|y|z")})
    monkeypatch.setattr(Gate, "_probe", lambda self, group: contextlib.nullcontext(open_web))
    monkeypatch.setattr(Gate, "_ready", lambda self, seconds=120: True)
    with pytest.raises(ProofFailed, match="stayed open"):
        made._down(ctx)
    assert stack.events == [("stop", "authentik-server"), ("start", "authentik-server")]


@pytest.mark.parametrize("change", ["release", "blueprint", "tls", "address"])
def test_security_changes_require_full_proof(gate, ctx, change):
    made, _, _ = gate
    ctx.facts["authentik.blueprints"] = "before"
    ctx.facts["gate.digest"] = made.digest(ctx)
    if change == "release":
        release = Release.from_models(ctx.layout, {}, {"configs": {"caddy": {"content": "no identity stripping"}}}, {})
        made = replace(made, release=release)
    elif change == "blueprint":
        ctx.facts["authentik.blueprints"] = "after"
    elif change == "tls":
        made = replace(made, verify_tls=False)
    else:
        made = replace(made, address=("127.0.0.1", 8443))
    assert made.check(ctx).status is Status.NEEDED


def test_route_sweep_cannot_be_omitted(gate):
    with pytest.raises(ValueError, match="every gated"):
        replace(gate[0], paths=())


def test_killed_outage_proof_leaves_restart_intent(gate, ctx, monkeypatch):
    made, stack, _ = gate
    monkeypatch.setattr(Gate, "_probe", lambda self, group: contextlib.nullcontext(FakeWeb()))

    def killed(verb, service):
        assert ctx.layout.restart_pending(service)
        raise KeyboardInterrupt

    stack.lifecycle = killed
    with pytest.raises(KeyboardInterrupt):
        made._down(ctx)
    assert ctx.layout.restart_pending("authentik-server")


def test_failed_reproof_stops_existing_tunnel_and_preserves_restart_intent(gate, ctx, monkeypatch):
    made, stack, _ = gate
    made = replace(made, release=Release.from_models(ctx.layout, {}, {"services": {"cloudflared": {}}}, {}))
    commands = []

    def call(command):
        commands.append(command)
        return "container-id" if "ps" in command else ""

    stack.call = call
    monkeypatch.setattr(Gate, "_ready", lambda self, seconds=120: False)
    assert not made.apply(ctx).ok
    assert commands[-1][-2:] == ["stop", "cloudflared"]
    assert ctx.layout.restart_pending("cloudflared")


def test_failure_after_check_also_closes_tunnel(gate, ctx, monkeypatch):
    made, stack, web = gate
    made = replace(made, release=Release.from_models(ctx.layout, {}, {"services": {"cloudflared": {}}}, {}))
    commands = []
    stack.call = lambda command: commands.append(command) or "container-id"
    web.routes["/radarr"] = reply(200)
    assert not made.verify(ctx).ok
    assert commands[-1][-2:] == ["stop", "cloudflared"]


def test_gate_refuses_to_run_without_setup_closure(gate, ctx):
    from types import SimpleNamespace
    from setup.engine.runner import run

    made, stack, _ = gate
    dependencies = [SimpleNamespace(id=name, needs=()) for name in ("phase.caddy", "AK-3")]
    with pytest.raises(ValueError, match="AK-9"):
        run([*dependencies, made], ctx)
    assert not stack.events


def test_owner_stopped_tunnel_does_not_gain_restart_intent(gate, ctx):
    made, stack, _ = gate
    made = replace(made, release=Release.from_models(ctx.layout, {}, {"services": {"cloudflared": {}}}, {}))
    commands = []
    stack.call = lambda command: commands.append(command) or ""
    made._close_tunnel(ctx)
    assert not ctx.layout.restart_pending("cloudflared")
    assert all("stop" not in command for command in commands)


def test_outage_proof_checks_application_routes_too(gate, ctx, monkeypatch):
    made, _, _ = gate
    web = FakeWeb({WHOAMI: reply(502), ADMIN: reply(502), "/sonarr": reply(502), "/radarr": reply(200)})
    monkeypatch.setattr(Gate, "_probe", lambda self, group: contextlib.nullcontext(web))
    monkeypatch.setattr(Gate, "_web", lambda self: web)
    monkeypatch.setattr(Gate, "_ready", lambda self, seconds=120: True)
    with pytest.raises(ProofFailed, match="/radarr.*stayed open"):
        made._down(ctx)
    assert not ctx.layout.restart_pending("authentik-server")


def test_a_family_member_on_a_real_admin_route_fails_the_proof(gate, monkeypatch):
    made, _, _ = gate
    made = replace(made, admin_paths=("/sonarr",))
    web = FakeWeb(
        {WHOAMI: reply(200, b"homelab-probe-family-abcdef|family|"), ADMIN: reply(403), "/sonarr": reply(200)}
    )
    monkeypatch.setattr(Gate, "_probe", lambda self, group: contextlib.nullcontext(web))
    with pytest.raises(ProofFailed, match="admin route: /sonarr"):
        made._signed_in()


def test_an_open_route_is_reported_even_when_authentik_does_not_come_back(gate, ctx, monkeypatch):
    made, _, _ = gate
    web = FakeWeb({WHOAMI: reply(502), ADMIN: reply(502), "/sonarr": reply(502), "/radarr": reply(200)})
    monkeypatch.setattr(Gate, "_probe", lambda self, group: contextlib.nullcontext(web))
    monkeypatch.setattr(Gate, "_web", lambda self: web)
    monkeypatch.setattr(Gate, "_ready", lambda self, seconds=120: False)
    with pytest.raises(ProofFailed, match="^/radarr: stayed open"):
        made._down(ctx)
    assert ctx.layout.restart_pending("authentik-server")
