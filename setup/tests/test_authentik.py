"""Authentik steps against a fake stack: ordering, secrets that stay out of argv, and convergence on rotation."""

import hashlib
import re

import pytest
import yaml

from setup.engine.contract import Secret, Status
from setup.wiring import authentik
from setup.wiring.authentik import (
    GOOGLE_ID,
    GOOGLE_SECRET,
    HASH,
    JELLYFIN,
    Applied,
    Blueprints,
    FirstBoot,
    Person,
    Secrets,
    render_blueprints,
)
from setup.wiring.stack import StackError

URLS = {
    "app_url": "https://app.example.test",
    "auth_url": "https://auth.example.test",
    "jellyfin_url": "https://jf.example.test:8443",
}


class FakeStack:
    """Records every call. `rows` answers psql by substring; a missing key raises like a missing table."""

    def __init__(self):
        self.calls, self.stdin, self.rows, self.flag = [], [], {}, False

    def once(self, service, *args, stdin=None, entrypoint=None):
        self.calls.append((service, args, entrypoint))
        self.stdin.append(stdin)
        if args == ("hash_password",):
            return "pbkdf2_sha256$1$salt$hash\n"
        if args[:1] == ("shell",):
            self.flag = True
        return ""

    def exec(self, service, *args, stdin=None):
        self.calls.append((service, args, None))
        return ""

    def restart(self, service):
        self.calls.append(("restart", service, None))

    def psql(self, sql):
        if "authentik_tenants_tenant" in sql:
            if not self.flag:
                raise StackError("relation does not exist")
            return [["true"]]
        for needle, rows in self.rows.items():
            if needle in sql:
                return rows
        return []


def release(tmp_path):
    folder = tmp_path / "blueprints"
    folder.mkdir()
    volume = {"type": "bind", "source": str(folder), "target": "/blueprints/custom"}
    from types import SimpleNamespace

    return SimpleNamespace(app={"services": {"authentik-worker": {"volumes": [volume]}}}), folder


def test_blueprints_render_with_the_given_origins_and_nothing_left_to_fill():
    files = render_blueprints(URLS, [])
    text = "\n".join(files.values())
    assert "https://jf.example.test:8443/sso/OID/redirect/authentik" in text
    assert not re.search(r"\$\{?[a-z_]+", text)  # no unfilled placeholder
    assert "password:" not in text.replace("password_hash", "")  # secrets come by !File only


@pytest.mark.parametrize("bad", ["http://app.example.test", "https://app.example.test/path", "app.example.test", ""])
def test_origins_must_be_https_with_no_path(bad):
    with pytest.raises(ValueError, match="app_url"):
        render_blueprints({**URLS, "app_url": bad}, [])


def test_every_blueprint_is_named_after_its_file_so_applied_can_find_it():
    yaml.add_multi_constructor("!", lambda loader, suffix, node: None, Loader=yaml.SafeLoader)
    for name, text in render_blueprints(URLS, [Person("kid", "kid@example.test", "Kid")]).items():
        assert yaml.safe_load(text)["metadata"]["name"] == authentik._instance(name), name


def test_people_are_quoted_and_put_in_groups():
    text = authentik.people_blueprint([Person("owner", "o@example.test", 'Ann "A" O', admin=True)])
    assert 'name: "Ann \\"A\\" O"' in text
    assert text.count("[name, admins]") == 1 and text.count("[name, family]") == 1


@pytest.mark.parametrize("args", [("Bad Name", "a@b.test", "x"), ("ok", "not-an-email", "x"), ("ok", "a@b.test", "")])
def test_a_person_with_unusable_fields_is_refused(args):
    with pytest.raises(ValueError):
        Person(*args)


def test_secrets_step_asks_for_google_once_each_and_makes_the_shared_oidc_secret(ctx):
    step = Secrets()
    first = step.apply(ctx)
    assert isinstance(first.prompt, Secret) and first.prompt.key == GOOGLE_ID
    assert ctx.secrets.exists(JELLYFIN)
    ctx.secrets.put(GOOGLE_ID, "client-id-value")
    assert step.apply(ctx).prompt.key == GOOGLE_SECRET
    ctx.secrets.put(GOOGLE_SECRET, "client-secret-value")
    assert step.apply(ctx).ok and step.check(ctx).status is Status.DONE


def test_first_boot_orders_hash_migrate_flag_and_keeps_the_password_out_of_argv(ctx):
    stack = FakeStack()
    step = FirstBoot(stack)
    assert step.check(ctx).status is Status.NEEDED  # empty database: no tables yet
    assert step.apply(ctx).ok
    kinds = [(args[0] if args[0] != "-m" else "migrate") for _, args, _ in stack.calls]
    assert kinds == ["hash_password", "migrate", "shell"]
    password = ctx.secrets.read(ctx.secrets.handle("AUTHENTIK_ADMIN_PASSWORD"))
    assert stack.stdin[0] == password
    assert not any(password in " ".join(args) for _, args, _ in stack.calls)
    assert ctx.secrets.read(ctx.secrets.handle(HASH)) == "pbkdf2_sha256$1$salt$hash"
    assert step.verify(ctx).ok and step.check(ctx).status is Status.DONE


def test_first_boot_rerun_reuses_the_hash_and_password(ctx):
    stack = FakeStack()
    FirstBoot(stack).apply(ctx)
    before = {n: ctx.secrets.read(ctx.secrets.handle(n)) for n in ("AUTHENTIK_ADMIN_PASSWORD", HASH)}
    stack.calls.clear()
    FirstBoot(stack).apply(ctx)
    assert all(args[0] != "hash_password" for _, args, _ in stack.calls)
    assert before == {n: ctx.secrets.read(ctx.secrets.handle(n)) for n in before}


def test_blueprint_replacement_updates_people_input_and_removes_obsolete_files(ctx, tmp_path):
    rel, folder = release(tmp_path)
    kid = Person("kid", "kid@example.test", "Kid")
    step = Blueprints(rel, URLS, [kid])
    assert step.check(ctx).status is Status.NEEDED
    assert step.apply(ctx).ok and step.check(ctx).status is Status.DONE
    assert "kid@example.test" in (folder / "30-people.yaml").read_text()
    (folder / "99-old.yaml").write_text("stale")
    assert Blueprints(rel, URLS, []).apply(ctx).ok
    assert not (folder / "99-old.yaml").exists()
    assert "kid@example.test" not in (folder / "30-people.yaml").read_text()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def applied_world(ctx, tmp_path):
    rel, folder = release(tmp_path)
    Blueprints(rel, URLS, []).apply(ctx)
    for name, value in ((GOOGLE_ID, "id"), (GOOGLE_SECRET, "secret"), (JELLYFIN, "oidc"), (HASH, "hash")):
        ctx.secrets.put(name, value)
    stack = FakeStack()
    names = [authentik._instance(p.name) for p in folder.glob("*.yaml")]
    stack.rows = {
        "blueprintinstance": [[n, "successful"] for n in names],
        "consumer_key": [[digest("id")]],
        "consumer_secret": [[digest("secret")]],
        "client_secret": [[digest("oidc")]],
        "akadmin": [[digest("hash")]],
    }
    step = Applied(stack, rel, timeout_s=0)
    ctx.facts["authentik.blueprints"] = step._revision()
    return stack, step


def test_applied_is_done_only_when_the_database_matches_every_secret_file(ctx, tmp_path):
    stack, step = applied_world(ctx, tmp_path)
    assert step.check(ctx).status is Status.DONE
    ctx.secrets.put(GOOGLE_SECRET, "rotated")
    assert step.check(ctx).status is Status.NEEDED


def test_rotation_restarts_the_worker_before_reapplying_every_blueprint(ctx, tmp_path):
    stack, step = applied_world(ctx, tmp_path)
    ctx.secrets.put(GOOGLE_SECRET, "rotated")
    assert step.apply(ctx).ok
    assert stack.calls[0] == ("restart", "authentik-worker", None)
    applied = [args[2] for _, args, _ in stack.calls[1:]]
    assert applied == sorted(f"/blueprints/custom/{p.name}" for p in (tmp_path / "blueprints").glob("*.yaml"))
    assert step.verify(ctx).ok is False  # the fake database still holds the old value: not claimed as done


def test_a_failed_blueprint_is_not_done(ctx, tmp_path):
    stack, step = applied_world(ctx, tmp_path)
    stack.rows["blueprintinstance"][0][1] = "error"
    assert step.check(ctx).status is Status.NEEDED


def test_changed_people_blueprint_requires_application_even_when_secrets_match(ctx, tmp_path):
    stack, step = applied_world(ctx, tmp_path)
    Blueprints(step.release, URLS, [Person("new", "new@example.test", "New")]).apply(ctx)
    assert step.check(ctx).status is Status.NEEDED
    assert step.apply(ctx).facts["authentik.blueprints"] == step._revision()
    assert stack.calls[0] == ("restart", "authentik-worker", None)


@pytest.mark.parametrize(
    "location,done", [("/flows/-/default/authentication/?next=/", True), ("/if/flow/initial-setup/", False)]
)
def test_setup_closure_is_checked_through_the_private_https_route(ctx, monkeypatch, location, done):
    from setup.wiring.web import Reply

    class Probe:
        def __init__(self, address, verify):
            assert address == ("127.0.0.1", 443) and verify

        def send(self, method, url):
            assert (method, url) == ("GET", "https://auth.example.test/")
            return Reply(302, {"location": location}, b"")

    monkeypatch.setattr(authentik, "Web", Probe)
    step = authentik.SetupClosed(("127.0.0.1", 443), "https://auth.example.test")
    assert (step.check(ctx).status is Status.DONE) is done
    assert step.verify(ctx).ok is done
