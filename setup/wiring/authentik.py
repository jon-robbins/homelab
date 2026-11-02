"""Authentik as engine steps (WS-01): first boot, secrets, blueprints, reapply and a closed setup page.

Order: AK-1 needs Postgres and must finish before the server starts, so the setup flag is true before the first
request. AK-0 and AK-2 must finish before the worker starts: it mounts their files. AK-3 and AK-9 need the running
containers. `build_steps` enforces these dependencies even when `requires` is omitted.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any, Mapping, Sequence

from ..engine.contract import CheckIsProof, Context, Result, Secret, State
from .stack import SERVER, WORKER, Stack, StackError
from .web import Web

CONFIG = Path(__file__).parents[1] / "config" / "authentik"
ADMIN, HASH, JELLYFIN = "AUTHENTIK_ADMIN_PASSWORD", "AUTHENTIK_ADMIN_PASSWORD_HASH", "JELLYFIN_OIDC_CLIENT_SECRET"
GOOGLE_ID, GOOGLE_SECRET = "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"
TARGET = "/blueprints/custom"
_URL = re.compile(r"https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d{1,5})?")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_GOOGLE = (
    "from authentik_sources_oauth_oauthsource o join authentik_core_source c "
    "on c.policybindingmodel_ptr_id = o.source_ptr_id where c.slug = 'google'"
)
# Secret files compared with the database by digest, so no value is ever read into a message.
_DIGESTS = (
    (GOOGLE_ID, f"select o.consumer_key {_GOOGLE}"),
    (GOOGLE_SECRET, f"select o.consumer_secret {_GOOGLE}"),
    (JELLYFIN, "select client_secret from authentik_providers_oauth2_oauth2provider where client_id = 'jellyfin'"),
    (HASH, "select password from authentik_core_user where username = 'akadmin'"),
)


@dataclass(frozen=True)
class Person:
    username: str
    email: str
    name: str
    admin: bool = False

    def __post_init__(self) -> None:
        if not (_NAME.fullmatch(self.username) and _EMAIL.fullmatch(self.email) and 0 < len(self.name) <= 80):
            raise ValueError(f"not a usable person: {self.username!r}")


def people_blueprint(people: Sequence[Person]) -> str:
    """Pre-created users (A-6). JSON strings are valid YAML, so names and emails need no escaping rules."""
    lines = ["version: 1", "metadata: {name: homelab - people}", "entries:"]
    lines += [
        "  - model: authentik_blueprints.metaapplyblueprint",
        "    attrs: {identifiers: {name: homelab - groups}, required: true}",
    ]
    for person in people:
        groups = ["admins", "family"] if person.admin else ["family"]
        found = ", ".join(f"!Find [authentik_core.group, [name, {g}]]" for g in groups)
        lines += [
            "  - model: authentik_core.user",
            "    state: present",
            f"    identifiers: {{username: {json.dumps(person.username)}}}",
            f"    attrs: {{name: {json.dumps(person.name)}, email: {json.dumps(person.email)}, path: users,"
            f" groups: [{found}]}}",
        ]
    return "\n".join(lines) + "\n"


def render_blueprints(urls: Mapping[str, str], people: Sequence[Person]) -> dict[str, str]:
    """File name -> text. `urls` needs app_url, auth_url and jellyfin_url: public https origins, no path."""
    for key in ("app_url", "auth_url", "jellyfin_url"):
        if not _URL.fullmatch(urls.get(key, "")):
            raise ValueError(f"{key} must be an https origin such as https://host:port")
    files = {p.name: Template(p.read_text(encoding="utf-8")).substitute(urls) for p in sorted(CONFIG.glob("*.yaml"))}
    return {**files, "30-people.yaml": people_blueprint(people)}


def _instance(name: str) -> str:
    return "homelab - " + name.removesuffix(".yaml").partition("-")[2]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _blueprint_dir(release: Any) -> Path:
    for volume in release.app["services"][WORKER].get("volumes", ()):
        if volume.get("target") == TARGET:
            return Path(volume["source"])
    raise ValueError("authentik-worker has no blueprint bind: add overrides/authentik-worker.yml from setup/config")


@dataclass(frozen=True)
class Secrets(CheckIsProof):
    """AK-0: the provider-issued Google client (G-4, asked for) and the shared Jellyfin OIDC secret (A-11, made)."""

    id: str = "AK-0"
    needs: tuple[str, ...] = ("engine.secrets",)

    def _missing(self, ctx: Context) -> list[str]:
        return [n for n in (GOOGLE_ID, GOOGLE_SECRET, JELLYFIN) if not ctx.secrets.exists(n)]

    def check(self, ctx: Context) -> State:
        return State.needed() if self._missing(ctx) else State.done()

    def apply(self, ctx: Context) -> Result:
        ctx.secrets.ensure(JELLYFIN)
        hint = "Google Cloud console, APIs & Services, Credentials, your OAuth client"
        for name, label in ((GOOGLE_ID, "Google OAuth client ID"), (GOOGLE_SECRET, "Google OAuth client secret")):
            if not ctx.secrets.exists(name):
                return Result.ask(Secret(name, label, hint))
        return Result.success("provider secrets present")


@dataclass(frozen=True)
class FirstBoot(CheckIsProof):
    """AK-1 (A-1, A-2): hash, migrate, close setup, all before the server's first request."""

    stack: Stack
    id: str = "AK-1"
    needs: tuple[str, ...] = ("phase.postgres",)

    def _closed(self) -> bool:
        try:
            return self.stack.psql("select flags->>'setup' from authentik_tenants_tenant") == [["true"]]
        except StackError:
            return False  # no tables yet: an empty database

    def check(self, ctx: Context) -> State:
        return State.done() if self._closed() and ctx.secrets.exists(HASH) else State.needed()

    def apply(self, ctx: Context) -> Result:
        if not ctx.secrets.exists(HASH):
            password = ctx.secrets.read(ctx.secrets.ensure(ADMIN))
            ctx.secrets.put(HASH, self.stack.once(SERVER, "hash_password", stdin=password).strip())
        self.stack.once(SERVER, "-m", "lifecycle.migrate", entrypoint="python")
        self.stack.once(SERVER, "shell", "-c", "from authentik.core.apps import Setup; Setup.set(True)")
        return Result.success("database migrated, first-run setup closed", {"authentik.breakglass": ADMIN})


@dataclass(frozen=True)
class Blueprints(CheckIsProof):
    """AK-2 (A-4, A-6): write the blueprint files the worker mounts. Changes are picked up by AK-3."""

    release: Any
    urls: Mapping[str, str]
    people: Sequence[Person] = ()
    id: str = "AK-2"
    needs: tuple[str, ...] = ("engine.dirs", "AK-0")

    def _files(self) -> dict[str, str]:
        return render_blueprints(self.urls, self.people)

    def _stale(self) -> list[str]:
        folder = _blueprint_dir(self.release)
        return [
            n for n, text in self._files().items() if not (folder / n).is_file() or (folder / n).read_text() != text
        ]

    def check(self, ctx: Context) -> State:
        return State.needed() if self._stale() else State.done()

    def apply(self, ctx: Context) -> Result:
        folder = _blueprint_dir(self.release)
        files = self._files()
        for name in self._stale():
            (folder / name).write_text(files[name], encoding="utf-8")
        for leftover in folder.glob("*.yaml"):
            if leftover.name not in files:
                leftover.unlink()  # remove obsolete files; database account revocation remains explicit
        return Result.success(f"{len(files)} blueprint files written")


@dataclass(frozen=True)
class Applied:
    """AK-3 (A-3, A-11, A-14): every blueprint applied, and the database holds the current secret files.

    Compose bind-mounts a secret as one file. Replacing that file leaves a running container on the old copy, so a
    mismatch is fixed by restarting the worker before re-applying.
    """

    stack: Stack
    release: Any
    id: str = "AK-3"
    needs: tuple[str, ...] = ("phase.authentik-worker", "AK-2")
    timeout_s: float = 90

    def _wanted(self) -> set[str]:
        return {_instance(p.name) for p in _blueprint_dir(self.release).glob("*.yaml")}

    def _revision(self) -> str:
        files = sorted(_blueprint_dir(self.release).glob("*.yaml"))
        return _digest(json.dumps({p.name: p.read_text() for p in files}, sort_keys=True))

    def _db_digest(self, sql: str) -> str:
        rows = self.stack.psql(f"select encode(sha256(convert_to(({sql}), 'UTF8')), 'hex')")
        return rows[0][0] if rows else ""

    def _current(self, ctx: Context) -> bool:
        """Every blueprint applied, and each secret in the database matches its file."""
        rows = self.stack.psql(
            "select name, status from authentik_blueprints_blueprintinstance where name like 'homelab - %'"
        )
        if {n for n, s in rows if s == "successful"} != self._wanted():
            return False
        return all(
            self._db_digest(sql) == _digest(ctx.secrets.read(ctx.secrets.handle(n)).strip()) for n, sql in _DIGESTS
        )

    def check(self, ctx: Context) -> State:
        if ctx.facts.get("authentik.blueprints") != self._revision():
            return State.needed()
        with contextlib.suppress(StackError):  # no database yet
            if self._current(ctx):
                return State.done()
        return State.needed()

    def _wait(self, ctx: Context) -> bool:
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            if self._current(ctx):
                return True
            time.sleep(3)
        return False

    def apply(self, ctx: Context) -> Result:
        if not ctx.facts.get("authentik.blueprints") and self._wait(ctx):
            return Result.success("blueprints applied", {"authentik.blueprints": self._revision()})
        self.stack.lifecycle("restart", WORKER)
        for path in sorted(_blueprint_dir(self.release).glob("*.yaml")):
            self.stack.exec(WORKER, "ak", "apply_blueprint", f"{TARGET}/{path.name}")
        return Result.success(
            "blueprints re-applied after restarting the worker", {"authentik.blueprints": self._revision()}
        )

    def verify(self, ctx: Context) -> Result:
        return Result.success() if self._wait(ctx) else Result.failure("blueprints or secrets do not match the files")


@dataclass(frozen=True)
class SetupClosed(CheckIsProof):
    """AK-9 (A-9): the front page sends a visitor to the login flow, never to /setup."""

    address: tuple[str, int]
    auth_url: str
    verify_tls: bool = True
    id: str = "AK-9"
    needs: tuple[str, ...] = ("phase.caddy", "AK-3")

    def _location(self) -> str:
        return Web(self.address, self.verify_tls).send("GET", self.auth_url + "/").location

    def check(self, ctx: Context) -> State:
        try:
            where = self._location()
        except (OSError, StackError) as error:
            return State.blocked(f"cannot reach Authentik: {error}")
        return State.done() if "/flows/-/default/authentication/" in where else State.needed()

    def apply(self, ctx: Context) -> Result:
        return Result.failure("first-run setup is open: the flag was not set before the server started")
