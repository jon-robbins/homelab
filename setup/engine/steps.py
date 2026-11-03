"""The core local-apply steps. Each one is idempotent and answers `check()` from the host, not from memory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from scripts.controller.planner import Plan, compose_commands

from .contract import CheckIsProof, Context, Result, State
from .ops import NETWORK, edge_compose, unhealthy

MARKER = ".homelab-mounted"
HEALTH_TIMEOUT_S = 180
COMPOSE_UP = ["up", "-d", "--no-deps", "--no-build", "--pull", "never"]


def _binds(release: Any) -> list[dict[str, Any]]:
    return [v for s in release.services.values() for v in s.get("volumes", ()) if v.get("type") == "bind"]


def _writable_binds(service: dict[str, Any]) -> list[Path]:
    return [Path(v["source"]) for v in service.get("volumes", ()) if v.get("type") == "bind" and not v.get("read_only")]


def _holds_data(path: Path) -> bool:
    return (path.is_file() and path.stat().st_size > 0) or (path.is_dir() and any(path.iterdir()))


def _owner(service: dict[str, Any]) -> tuple[int, int]:
    """uid:gid the service runs as: its `user:`, else PUID/PGID, else whoever runs setup."""
    env = service.get("environment", {})
    user = service.get("user", f"{env.get('PUID', os.geteuid())}:{env.get('PGID', os.getegid())}")
    uid, _, gid = str(user).partition(":")
    return int(uid), int(gid or uid)


def _is_marker(path: str) -> bool:
    return path.endswith("/" + MARKER)


@dataclass(frozen=True)
class WriteRelease(CheckIsProof):
    release: Any
    id: str = "engine.release"
    needs: tuple[str, ...] = ()

    def check(self, ctx: Context) -> State:
        return State.done() if self.release.complete() else State.needed()

    def apply(self, ctx: Context) -> Result:
        self.release.write()
        return Result.success(f"wrote release {self.release.directory.name}")


@dataclass(frozen=True)
class Network(CheckIsProof):
    ops: Any
    id: str = "engine.network"
    needs: tuple[str, ...] = ()

    def check(self, ctx: Context) -> State:
        if not self.ops.network_exists(NETWORK):
            return State.needed()
        if self.ops.network_ours(NETWORK):
            return State.done()
        return State.blocked(
            f"an existing {NETWORK} network was not created by local setup; remove it or use another host"
        )

    def apply(self, ctx: Context) -> Result:
        self.ops.create_network(NETWORK)
        return Result.success(f"network {NETWORK} ready")


@dataclass(frozen=True)
class Dirs(CheckIsProof):
    """Create data directories only after the operator has mounted and marked the disks."""

    release: Any
    id: str = "engine.dirs"
    needs: tuple[str, ...] = ("engine.release",)

    def _missing(self) -> tuple[list[Path], list[Path]]:
        sources = {v["source"] for v in _binds(self.release)}
        markers = [Path(s) for s in sorted(sources) if _is_marker(s) and not Path(s).exists()]
        dirs = [Path(s) for s in sorted(sources) if not _is_marker(s) and not Path(s).exists()]
        return markers, dirs

    def check(self, ctx: Context) -> State:
        markers, dirs = self._missing()
        if markers:
            return State.blocked(f"mount the correct disk and create its marker: {markers[0]}")
        return State.needed() if dirs else State.done()

    def _owners(self) -> dict[Path, tuple[int, int]]:
        owners: dict[Path, tuple[int, int]] = {}
        for service in self.release.services.values():
            for path in _writable_binds(service):
                if owners.setdefault(path, _owner(service)) != _owner(service):
                    raise ValueError(f"conflicting directory owners: {path}")
        return owners

    def apply(self, ctx: Context) -> Result:
        markers, dirs = self._missing()
        if markers:
            return Result.failure(f"mount the correct disk and create its marker: {markers[0]}")
        owners = self._owners()
        for directory in dirs:
            uid, gid = owners.get(directory, (os.geteuid(), os.getegid()))
            if os.geteuid() != 0 and (uid, gid) != (os.geteuid(), os.getegid()):
                return Result.failure(f"run as root to create {directory} for {uid}:{gid}")
        for directory in dirs:
            directory.mkdir(parents=True, exist_ok=True)
            if directory in owners and os.geteuid() == 0:
                os.chown(directory, *owners[directory])
        return Result.success(f"created {len(dirs)} directories")


@dataclass(frozen=True)
class Secrets(CheckIsProof):
    """Create the secrets that must be random and fixed at first start. Provider-issued ones are never invented."""

    release: Any
    generated: tuple[str, ...]
    ops: Any
    id: str = "engine.secrets"
    needs: tuple[str, ...] = ("engine.release",)

    def _wanted(self, ctx: Context) -> list[str]:
        files = self.release.secrets.values()
        names = {Path(f["file"]).name for f in files if Path(f["file"]).parent == ctx.secrets.directory}
        return sorted(names & set(self.generated))

    def check(self, ctx: Context) -> State:
        missing = [n for n in self._wanted(ctx) if not ctx.secrets.exists(n)]
        lost = [n for n in missing if self._started(ctx, n)]
        if lost:
            return State.blocked(
                f"generated secret(s) missing after first start: {', '.join(lost)}; restore the original files"
            )
        return State.needed() if missing else State.done()

    def _started(self, ctx: Context, name: str) -> bool:
        """Whether a service using secret `name` has already run or written data, so a new value would break it."""
        if ctx.checkpoints.facts().get(f"secret.created.{name}"):
            return True
        files = self.release.secrets
        for service, config in self.release.services.items():
            if name not in {Path(files[e["source"]]["file"]).name for e in config.get("secrets", ())}:
                continue
            inspected = self.ops.inspect(service, self.release.project(service))  # an error aborts, never "absent"
            if inspected and inspected.get("State", {}).get("Status") != "created":
                return True
            if any(map(_holds_data, _writable_binds(config))):
                return True
        return False

    def apply(self, ctx: Context) -> Result:
        made = [n for n in self._wanted(ctx) if not ctx.secrets.exists(n)]
        lost = [n for n in made if self._started(ctx, n)]
        if lost:
            return Result.failure(
                f"generated secret(s) missing after first start: {', '.join(lost)}; restore the original files"
            )
        for name in made:
            ctx.secrets.ensure(name)
        return Result.success(
            f"generated {len(made)} secret(s): {', '.join(made)}",
            {f"secret.created.{name}": True for name in made},
        )


@dataclass(frozen=True)
class Phase:
    """Start a group of services one at a time with --no-deps; `docker compose up --wait` waits for each to be healthy."""

    id: str
    needs: tuple[str, ...]
    release: Any
    services: tuple[str, ...]
    ops: Any

    def _project(self, service: str) -> str:
        return self.release.project(service)

    def _wanted_running(self, service: str) -> bool:
        return self.release.site.get("services", {}).get(service, {}).get("state", "running") == "running"

    def _missing_secrets(self, ctx: Context) -> list[str]:
        models = self.release.secrets
        files = [models[e["source"]]["file"] for s in self.services for e in self._service(s).get("secrets", ())]
        return sorted({Path(f).name for f in files if not (Path(f).is_file() and Path(f).stat().st_size)})

    def _service(self, service: str) -> dict[str, Any]:
        return self.release.services[service]

    def _has_data(self, service: str) -> bool:
        return any(map(_holds_data, _writable_binds(self._service(service))))

    def _stateful(self, service: str) -> bool:
        return self._service(service).get("labels", {}).get("homelab.state") == "stateful"

    def _has_state(self, service: str) -> bool:
        return self.ops.inspect(service, self._project(service)) is not None or self._has_data(service)

    def _stale(self, service: str) -> bool:
        view = self.ops.container(self._project(service), service)
        return view is None or view.config_hash != self.ops.config_hash(
            self.release.directory, self._project(service), service
        )

    def _split(self) -> tuple[list[str], list[str]]:
        todo, stopped = [], []
        for service in self.services:
            view, running = self.ops.container(self._project(service), service), self._wanted_running(service)
            if view and view.status == "exited":
                if running:
                    stopped.append(service)  # the owner stopped it; never restart it
                continue
            # _stale is true when there is no container, so `view` is set for the status check.
            if self._stale(service) or (running and view.status == "created"):
                todo.append(service)
        return todo, stopped

    def check(self, ctx: Context) -> State:
        if not self.release.complete():
            return State.needed()
        if missing := self._missing_secrets(ctx):
            return State.blocked(
                f"secret file(s) not present: {', '.join(missing)}. A provider step must supply them first."
            )
        todo, stopped = self._split()
        if stopped:
            return State.blocked(
                f"{', '.join(stopped)} stopped. Setup does not restart a service you stopped; start it yourself, or use docker rm on the failed container before retrying."
            )
        if todo:
            return State.needed()
        return State.needed() if self._unhealthy() else State.done()

    def _unhealthy(self, services: Sequence[str] | None = None) -> list[str]:
        """`service: reason` for each service meant to run that is not serving right now. Read-only."""
        chosen = filter(self._wanted_running, self.services if services is None else services)
        reasons = {s: unhealthy(self.ops.inspect(s, self._project(s))) for s in chosen}
        return [f"{s}: {why}" for s, why in reasons.items() if why]

    def _command(self, service: str) -> list[str]:
        running, directory = self._wanted_running(service), self.release.directory
        if self.release.tier(service) == "app":
            plan_ = Plan((service,), (), (), ()) if running else Plan((), (service,), (), ())
            command = compose_commands(plan_, directory)[0]
        else:
            up = COMPOSE_UP if running else ["up", "--no-deps", "--no-start", "--no-build", "--pull", "never"]
            command = [*edge_compose(directory), *up, service]
        wait = ["--wait", "--wait-timeout", str(HEALTH_TIMEOUT_S)] if running else []  # Compose waits for health
        return [*command[:-1], *wait, service]

    def apply(self, ctx: Context) -> Result:
        todo, stopped = self._split()
        if stopped:
            return Result.failure(f"owner-stopped services: {stopped}")
        # Recreating a container over existing data needs a backup first; restarting one that already exists does not.
        if risky := [s for s in todo if self._stateful(s) and self._stale(s) and self._has_state(s)]:
            return Result.failure(f"{', '.join(risky)} would be recreated over existing data; back it up, then retry")
        for tier in ("app", "edge"):
            self.ops.pull(
                self.release.directory,
                tuple(s for s in todo if self.release.tier(s) == tier),
                tier,
            )
        # `up --wait` on an up-to-date container recreates nothing, so it also waits out one still starting.
        waiting = [s for s in self.services if s not in todo and self._wanted_running(s) and self._unhealthy([s])]
        for service in [*todo, *waiting]:
            self.ops.run(self._command(service))
        return Result.success(f"started {', '.join(todo) or 'nothing'}")

    def verify(self, ctx: Context) -> Result:
        if failing := self._unhealthy():
            return Result.failure("; ".join(failing))
        return Result.success()
