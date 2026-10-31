"""Docker effects for local apply, built on the controller's adapter, with health judged strictly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import time

from scripts.controller.reconcile import SubprocessOps

NETWORK = "homelab_net"


@dataclass(frozen=True)
class Verdict:
    status: str  # ok | wait | fail
    reason: str = ""


def judge(inspected: dict | None, settle_s: float, now: datetime) -> Verdict:
    """A running container is not a healthy one. Needs a passing healthcheck, or, with none, a quiet `settle_s`."""
    if inspected is None:
        return Verdict("fail", "no container")
    state = inspected.get("State", {})
    if state.get("Status") != "running":
        return Verdict("fail", f"container is {state.get('Status') or 'unknown'}")
    health = state.get("Health", {}).get("Status")
    if health in ("healthy", "unhealthy"):
        return Verdict("ok") if health == "healthy" else Verdict("fail", "unhealthy")
    if health:
        return Verdict("wait", "health check still starting")
    if inspected.get("RestartCount", 0):
        return Verdict("fail", "restarting")
    started = datetime.fromisoformat(state["StartedAt"][:26].rstrip("Z") + "+00:00")
    age = (now - started).total_seconds()
    return Verdict("ok") if age >= settle_s else Verdict("wait", f"up for {age:.0f}s of {settle_s:.0f}s")


class LocalOps(SubprocessOps):
    def __init__(self, settle_s: float = 10):
        self.settle_s = settle_s
        self._hashes: dict[tuple[str, str, str], str] = {}

    def config_hash(self, release_dir, project, service):
        key = (str(release_dir), project, service)  # a release never changes, so asking Compose once is enough
        if key not in self._hashes:
            self._hashes[key] = super().config_hash(release_dir, project, service)
        return self._hashes[key]

    def pull(self, release_dir, services, tier="app"):
        if tier == "app":
            return super().pull(release_dir, services)
        if services:
            model = str(Path(release_dir) / "edge.json")
            self.run(["docker", "compose", "-p", "homelab-edge", "-f", model, "pull", *services])

    def network_exists(self, name):
        return subprocess.run(["docker", "network", "inspect", name], capture_output=True).returncode == 0

    def ensure_network(self, name):
        if not self.network_exists(name):
            self.run(["docker", "network", "create", name])

    def wait_healthy(self, project, service, timeout_s):
        deadline = time.monotonic() + timeout_s
        while True:
            verdict = judge(self.inspect(service, project), self.settle_s, datetime.now(timezone.utc))
            if verdict.status == "ok":
                return verdict
            if verdict.status == "fail" or time.monotonic() >= deadline:
                return Verdict("fail", f"{service}: {verdict.reason}")
            time.sleep(1)
