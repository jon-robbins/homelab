"""Apply verified release candidates to a host."""

from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import time
from typing import Any, Protocol

from scripts.controller.feed import fetch_newest
from scripts.controller.materialize import materialize
from scripts.controller.planner import ContainerView, DockerAdapter, Plan, compose_commands, plan
from scripts.controller.state import State


class Ops(DockerAdapter, Protocol):
    def reachable(self) -> bool: ...
    def free_pct(self, path: str) -> float: ...
    def pull(self, release_dir: Path, services: tuple[str, ...]) -> None: ...
    def stop(self, service: str) -> None: ...
    def start(self, service: str) -> None: ...
    def run(self, argv: list[str]) -> None: ...
    def healthy(self, service: str, timeout_s: int) -> bool: ...
    def snapshot(self, service: str, tag: str) -> str: ...
    def restore(self, service: str, tag: str) -> None: ...


class Pinger(Protocol):
    def ok(self) -> None: ...
    def fail(self, reason: str) -> None: ...


class ServiceFailed(RuntimeError):
    def __init__(self, service: str, message: str) -> None:
        super().__init__(message)
        self.service = service


def _link(path: Path, target: Path | None) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    if target is None:
        path.unlink(missing_ok=True)
        return
    temporary.symlink_to(target.resolve())
    os.replace(temporary, path)


def _services(release: Path) -> dict[str, dict[str, Any]]:
    return json.loads((release / "app.json").read_bytes()).get("services", {})


def _ping(pinger: Pinger, causes: list[str]) -> str:
    pinger.fail("; ".join(causes)) if causes else pinger.ok()
    return "ok"


def _degraded(state: State, sequence: int, service: str, snapshot: str | None, reason: str, pinger: Pinger) -> str:
    text = f"service: {service}\nsnapshot: {snapshot or 'none'}\nreason: {reason}\nnext: homelabctl rollback {service}"
    state.set_highwater(sequence)
    state.write("DEGRADED", text + "\n")
    state.remove("applying")
    pinger.fail(f"service: {service}\nnext: homelabctl rollback {service}")
    return text


def _preapply(state: State, cause: str, pinger: Pinger, causes: list[str]) -> str:
    # Nothing has been changed yet: keep highwater, retry next tick, alert on the third failure in a row.
    count = int(state.read("failures", "0").strip()) + 1
    state.write("failures", f"{count}\n")
    message = f"pre-apply failed {count}×: {cause}"
    if count >= 3:
        pinger.fail(message)
    else:
        _ping(pinger, causes)
    return message


def _halted(state: State, ops: Ops, pinger: Pinger) -> str | None:
    # Steps 1-2 plus Docker reachability: the reason to stop this tick, or None.
    applying = state.read("applying")
    if applying is not None:
        reason = f"interrupted apply of seq {applying.strip()}"
        state.set_highwater(int(applying.strip()))
        state.write("DEGRADED", reason + "\n")
    else:
        reason = state.read("DEGRADED", "").strip() or (None if ops.reachable() else "docker unreachable")
    if reason:
        pinger.fail(reason)
    return reason


def _causes(ops: Ops, current: Path) -> list[str]:
    # Step 3: conditions that fail the deadman ping without stopping the tick.
    causes = [f"low free space on {path}" for path in ("/", "/mnt/media-hdd") if ops.free_pct(path) < 10]
    if current.is_symlink() or current.is_dir():
        causes.extend(plan(current, ops).failures)
    return causes


def _advance(state: State, releases: Path, current: Path, sequence: int) -> None:
    _link(releases / "previous", releases / "current" if current.exists() else None)
    _link(releases / "current", releases / str(sequence))
    state.set_highwater(sequence)
    state.write("failures", "0\n")


def _snapshot(ops: Ops, release: Path, p: Plan, sequence: int) -> dict[str, str]:
    # Step 7: free space, pull, and a snapshot of each stateful service about to be recreated.
    if ops.free_pct("/") < 10:
        raise RuntimeError("low free space on /")
    ops.pull(release, tuple(sorted({*p.recreate, *p.create_stopped})))
    services, snapshots = _services(release), {}
    for service in p.recreate:
        if services.get(service, {}).get("labels", {}).get("homelab.state") == "stateful":
            ops.stop(service)
            try:
                snapshots[service] = ops.snapshot(service, f"seq-{sequence}")
            except Exception:
                ops.start(service)
                raise
    return snapshots


def _apply(ops: Ops, release: Path, p: Plan) -> None:
    # Steps 8-9: run the Compose commands, then wait for every recreated service to become healthy.
    for argv in compose_commands(p, release):
        ops.run(argv)
    for service in p.recreate:
        if not ops.healthy(service, 180):
            raise ServiceFailed(service, f"{service} failed health check")


def reconcile(state: State, feed_policy: Any, releases: Path, ops: Ops, pinger: Pinger, *, now: bool) -> str:
    releases = Path(releases)
    with state.lock():
        if halted := _halted(state, ops, pinger):
            return halted
        current = releases / "current"
        causes = _causes(ops, current)
        try:  # steps 4-7: no container has been touched yet
            candidate = fetch_newest(state.highwater(), feed_policy)
            if candidate is None:
                return _ping(pinger, causes)
            release = materialize(candidate, releases)
            p = plan(release, ops)
            if state.read("observe-only") is not None and not now:
                return _ping(pinger, causes)
            if not p.recreate and not p.create_stopped:
                _advance(state, releases, current, candidate.sequence)
                return _ping(pinger, causes)
            snapshots = _snapshot(ops, release, p, candidate.sequence)
        except Exception as error:
            return _preapply(state, str(error), pinger, causes)
        try:  # steps 8-9: containers change; a failure halts with the rollback command
            _link(releases / "previous", current if current.exists() else None)
            state.write("applying", f"{candidate.sequence}\n")
            _apply(ops, release, p)
        except Exception as error:
            failed = getattr(error, "service", None) or next(iter((*p.recreate, *p.create_stopped)), "unknown")
            return _degraded(state, candidate.sequence, failed, snapshots.get(failed), str(error), pinger)
        _advance(state, releases, current, candidate.sequence)
        state.remove("applying")
        return _ping(pinger, causes)


def _out(*argv: str) -> str:
    return subprocess.check_output(argv, text=True, stderr=subprocess.DEVNULL)


class SubprocessOps:
    """The real adapter: docker, docker compose and homelab-backup. Tests use recording fakes instead."""

    def reachable(self):
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0

    def free_pct(self, path):
        value = os.statvfs(path)
        return value.f_bavail * 100 / value.f_blocks

    def path_exists(self, path):
        return Path(path).exists()

    def config_hash(self, release_dir, project, service):
        model = Path(release_dir).resolve() / ("edge.json" if project == "homelab-edge" else "app.json")
        return _out("docker", "compose", "-p", project, "-f", str(model), "config", "--hash", service).split()[-1]

    def _container_id(self, project, service):
        labels = [f"label=com.docker.compose.project={project}", f"label=com.docker.compose.service={service}"]
        ids = _out("docker", "ps", "-a", "--filter", labels[0], "--filter", labels[1], "--format", "{{.ID}}")
        return next(iter(ids.splitlines()), None)

    def inspect(self, service, project="homelab"):  # also used by scripts/migration/shadow
        identifier = self._container_id(project, service)
        return json.loads(_out("docker", "inspect", identifier))[0] if identifier else None

    def container(self, project, service):
        try:
            inspected = self.inspect(service, project)
        except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
            return None
        if inspected is None:
            return None
        labels, state = inspected.get("Config", {}).get("Labels") or {}, inspected.get("State", {})
        return ContainerView(
            labels.get("com.docker.compose.config-hash"), state.get("Status", ""), state.get("Error", "")
        )

    def pull(self, release_dir, services):
        if services:
            self.run(["docker", "compose", "-f", str(Path(release_dir) / "app.json"), "pull", *services])

    def _docker(self, verb, service):  # no-op when the service has no container
        if (identifier := self._container_id("homelab", service)) is not None:
            self.run(["docker", verb, identifier])

    def stop(self, service):
        self._docker("stop", service)

    def start(self, service):
        self._docker("start", service)

    def run(self, argv):
        subprocess.run(argv, check=True)

    def healthy(self, service, timeout_s):
        deadline = time.monotonic() + timeout_s
        while True:
            state = (self.inspect(service) or {}).get("State", {})
            health = state.get("Health", {}).get("Status", "none")
            if state.get("Status") == "running" and health in ("healthy", "none"):
                return True
            if state.get("Status") == "exited" or health == "unhealthy" or time.monotonic() >= deadline:
                return False
            time.sleep(max(0, min(1, deadline - time.monotonic())))

    def snapshot(self, service, tag):
        return _out("homelab-backup", "--service", service, "--tag", tag).splitlines()[-1]

    def restore(self, service, tag):
        self.run(["homelab-backup", "--restore", service, "--tag", tag])
