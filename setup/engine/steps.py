"""The core local-apply steps. Each one is idempotent and answers `check()` from the host, not from memory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.controller.planner import Plan, compose_commands, plan

from .contract import Context, Result, State
from .ops import NETWORK

MARKER = ".homelab-mounted"
HEALTH_TIMEOUT_S = 180
COMPOSE_UP = ["up", "-d", "--no-deps", "--no-build", "--pull", "never"]


def _binds(release: Any) -> list[dict[str, Any]]:
    models = (release.app, release.edge)
    return [
        v
        for m in models
        for s in m.get("services", {}).values()
        for v in s.get("volumes", ())
        if v.get("type") == "bind"
    ]


def _is_marker(path: str) -> bool:
    return path.endswith("/" + MARKER)


@dataclass(frozen=True)
class WriteRelease:
    release: Any
    id: str = "engine.release"
    needs: tuple[str, ...] = ()

    def check(self, ctx: Context) -> State:
        return State.done() if self.release.complete() else State.needed()

    def apply(self, ctx: Context) -> Result:
        self.release.write()
        return Result.success(f"wrote release {self.release.directory.name}")

    def verify(self, ctx: Context) -> Result:
        return Result.success() if self.release.complete() else Result.failure("release files are missing")


@dataclass(frozen=True)
class Network:
    ops: Any
    planned: Any = None
    id: str = "engine.network"
    needs: tuple[str, ...] = ()

    def check(self, ctx: Context) -> State:
        if not self.ops.network_exists(NETWORK):
            return State.needed()
        if self.ops.network_ready(NETWORK, ctx.layout.setup_dir):
            return State.done()
        return State.blocked(f"existing {NETWORK} is not owned by local setup or does not match its saved network plan")

    def apply(self, ctx: Context) -> Result:
        spec = self.planned or self.ops.network_spec(ctx.layout.setup_dir)
        self.ops.ensure_network(NETWORK, ctx.layout.setup_dir, spec)
        return Result.success(f"network {NETWORK} ready")

    def verify(self, ctx: Context) -> Result:
        return (
            Result.success()
            if self.ops.network_ready(NETWORK, ctx.layout.setup_dir)
            else Result.failure(f"{NETWORK} does not match its saved local setup network plan")
        )


@dataclass(frozen=True)
class Dirs:
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
        owners = {}
        for model in (self.release.app, self.release.edge):
            for service in model.get("services", {}).values():
                env = service.get("environment", {})
                user = service.get(
                    "user",
                    f"{env.get('PUID', os.geteuid())}:{env.get('PGID', os.getegid())}",
                )
                uid, _, gid = str(user).partition(":")
                identity = (int(uid), int(gid or uid))
                for volume in service.get("volumes", ()):
                    if volume.get("type") == "bind" and not volume.get("read_only"):
                        path = Path(volume["source"])
                        if path in owners and owners[path] != identity:
                            raise ValueError(f"conflicting directory owners: {path}")
                        owners[path] = identity
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

    def verify(self, ctx: Context) -> Result:
        markers, dirs = self._missing()
        return Result.failure(f"still missing: {markers + dirs}") if markers or dirs else Result.success()


@dataclass(frozen=True)
class Secrets:
    """Create the secrets that must be random and fixed at first start. Provider-issued ones are never invented."""

    release: Any
    generated: tuple[str, ...]
    ops: Any
    id: str = "engine.secrets"
    needs: tuple[str, ...] = ("engine.release",)

    def _wanted(self, ctx: Context) -> list[str]:
        files = {
            **self.release.app.get("secrets", {}),
            **self.release.edge.get("secrets", {}),
        }
        names = {Path(f["file"]).name for f in files.values() if Path(f["file"]).parent == ctx.secrets.directory}
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
        if ctx.checkpoints.facts().get(f"secret.created.{name}"):
            return True
        for project, model in (("homelab", self.release.app), ("homelab-edge", self.release.edge)):
            files = model.get("secrets", {})
            for service, config in model.get("services", {}).items():
                if not any(Path(files[entry["source"]]["file"]).name == name for entry in config.get("secrets", ())):
                    continue
                inspected = self.ops.inspect(service, project)  # an inspection error must abort, never mean absent
                if inspected and inspected.get("State", {}).get("Status") != "created":
                    return True
                for volume in config.get("volumes", ()):
                    if volume.get("type") != "bind" or volume.get("read_only"):
                        continue
                    path = Path(volume["source"])
                    if (path.is_file() and path.stat().st_size) or (path.is_dir() and any(path.iterdir())):
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

    def verify(self, ctx: Context) -> Result:
        missing = [n for n in self._wanted(ctx) if not ctx.secrets.exists(n)]
        return Result.failure(f"not written: {missing}") if missing else Result.success()


@dataclass(frozen=True)
class Phase:
    """Start a group of services one at a time with --no-deps, waiting for real health after each."""

    id: str
    needs: tuple[str, ...]
    release: Any
    services: tuple[str, ...]
    ops: Any

    def _project(self, service: str) -> str:
        return "homelab" if self.release.tier(service) == "app" else "homelab-edge"

    def _wanted_running(self, service: str) -> bool:
        return self.release.site.get("services", {}).get(service, {}).get("state", "running") == "running"

    def _missing_secrets(self, ctx: Context) -> list[str]:
        models = {
            **self.release.app.get("secrets", {}),
            **self.release.edge.get("secrets", {}),
        }
        files = [models[e["source"]]["file"] for s in self.services for e in self._service(s).get("secrets", ())]
        return sorted({Path(f).name for f in files if not (Path(f).is_file() and Path(f).stat().st_size)})

    def _service(self, service: str) -> dict[str, Any]:
        return {
            **self.release.app.get("services", {}),
            **self.release.edge.get("services", {}),
        }[service]

    def _has_data(self, service: str) -> bool:
        for volume in self._service(service).get("volumes", ()):
            if volume.get("type") != "bind" or volume.get("read_only"):
                continue
            path = Path(volume["source"])
            if (path.is_file() and path.stat().st_size) or (path.is_dir() and any(path.iterdir())):
                return True
        return False

    def _stale(self, service: str) -> bool:
        view = self.ops.container(self._project(service), service)
        return view is None or view.config_hash != self.ops.config_hash(
            self.release.directory, self._project(service), service
        )

    def _failures(self, planned: Plan) -> list[str]:
        return [
            f
            for f in planned.failures
            if f.startswith("media disk") or any(f.startswith(f"data dir missing: {s}:") for s in self.services)
        ]

    def _split(self, ctx: Context) -> tuple[list[str], list[str]]:
        todo, stopped = [], []
        for service in self.services:
            view = self.ops.container(self._project(service), service)
            if view and view.status == "exited" and not ctx.layout.restart_pending(service):
                if self._wanted_running(service):
                    stopped.append(service)
            elif (
                self._stale(service)
                or (ctx.layout.restart_pending(service) and view.status != "running")
                or self._wanted_running(service)
                and view.status == "created"
            ):
                todo.append(service)
        return todo, stopped

    def check(self, ctx: Context) -> State:
        if not self.release.complete():
            return State.needed()
        if missing := self._missing_secrets(ctx):
            return State.blocked(
                f"secret file(s) not present: {', '.join(missing)}. A provider step must supply them first."
            )
        if failures := self._failures(plan(self.release.directory, self.ops)):
            return State.blocked(failures[0])
        todo, stopped = self._split(ctx)
        if stopped:
            return State.blocked(
                f"{', '.join(stopped)} stopped. Setup does not restart a service you stopped; start it yourself, or use docker rm on the failed container before retrying."
            )
        if todo:
            return State.needed()
        unhealthy = [
            s
            for s in self.services
            if self._wanted_running(s) and self.ops.wait_healthy(self._project(s), s, 0).status != "ok"
        ]
        return State.needed() if unhealthy else State.done()

    def _command(self, service: str) -> list[str]:
        directory = self.release.directory
        if self.release.tier(service) == "app":
            stopped = not self._wanted_running(service)
            return compose_commands(
                Plan((), (service,), (), ()) if stopped else Plan((service,), (), (), ()),
                directory,
            )[0]
        return [
            "docker",
            "compose",
            "-p",
            "homelab-edge",
            "-f",
            str(directory / "edge.json"),
            *(
                COMPOSE_UP
                if self._wanted_running(service)
                else ["up", "--no-deps", "--no-start", "--no-build", "--pull", "never"]
            ),
            service,
        ]

    def _snapshot(self, ctx: Context, service: str, enabled: bool) -> None:
        if self._service(service).get("labels", {}).get("homelab.state") != "stateful":
            return
        if self.ops.inspect(service, self._project(service)) is None:
            if self._has_data(service):
                raise RuntimeError(f"{service} has existing data but no container; take a manual snapshot before apply")
            return
        if self.release.tier(service) != "app":
            raise RuntimeError("stateful edge changes require a manual snapshot before apply")
        if not enabled:
            return
        ctx.layout.set_restart_pending(service, True)
        self.ops.stop(service)
        self.ops.snapshot(service, f"local-{self.release.directory.name}")

    def apply(self, ctx: Context) -> Result:
        todo, stopped = self._split(ctx)
        if stopped:
            return Result.failure(f"owner-stopped services: {stopped}")
        stateful = [s for s in todo if self._service(s).get("labels", {}).get("homelab.state") == "stateful"]
        existing = [s for s in stateful if self.ops.inspect(s, self._project(s)) is not None or self._has_data(s)]
        if any(self.release.tier(s) == "edge" for s in existing):
            return Result.failure("stateful edge changes require a manual snapshot before apply")
        snapshot = bool(existing)
        can_snapshot = self.ops.can_snapshot()
        if snapshot and not can_snapshot:
            return Result.failure("no local snapshot is configured; existing stateful data will not be changed")
        for tier in ("app", "edge"):
            self.ops.pull(
                self.release.directory,
                tuple(s for s in todo if self.release.tier(s) == tier),
                tier,
            )
        for service in todo:
            self._snapshot(ctx, service, can_snapshot)
            self.ops.run(self._command(service))
            if self._wanted_running(service):
                verdict = self.ops.wait_healthy(self._project(service), service, HEALTH_TIMEOUT_S)
                if verdict.status != "ok":
                    return Result.failure(verdict.reason)
            ctx.layout.set_restart_pending(service, False)
        return Result.success(f"started {', '.join(todo) or 'nothing'}")

    def verify(self, ctx: Context) -> Result:
        for service in filter(self._wanted_running, self.services):
            verdict = self.ops.wait_healthy(self._project(service), service, HEALTH_TIMEOUT_S)
            if verdict.status != "ok":
                return Result.failure(verdict.reason)
            ctx.layout.set_restart_pending(service, False)
        return Result.success()
