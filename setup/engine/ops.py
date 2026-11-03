"""Docker effects for local apply, built on the controller's adapter, with health judged strictly."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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


def edge_compose(release_dir) -> list[str]:
    """`docker compose` for the edge tier of a release. The app tier goes through the controller's planner."""
    return ["docker", "compose", "-p", "homelab-edge", "-f", str(Path(release_dir) / "edge.json")]


class LocalOps(SubprocessOps):
    def __init__(self, settle_s: float = 10):
        self.settle_s = settle_s
        self._hashes: dict[tuple[str, str, str], str] = {}

    def config_hash(self, release_dir, project, service):
        key = (
            str(release_dir),
            project,
            service,
        )  # a release never changes, so asking Compose once is enough
        if key not in self._hashes:
            self._hashes[key] = super().config_hash(release_dir, project, service)
        return self._hashes[key]

    def foreign_containers(self, releases: Path) -> list[str]:
        """Containers in the owner's Compose projects whose Compose files are not under our release directory."""
        label = "com.docker.compose.project.config_files"
        rows = []
        for project in ("homelab", "homelab-edge"):
            # Docker ANDs repeated label filters, so each project needs its own query.
            argv = [
                "docker",
                "ps",
                "-a",
                "--format",
                f'{{{{.Names}}}}\t{{{{.Label "{label}"}}}}',
                "--filter",
                f"label=com.docker.compose.project={project}",
            ]
            listed = subprocess.run(argv, capture_output=True, text=True)
            if listed.returncode != 0:
                raise RuntimeError("cannot inspect Docker ownership; restore Docker access before applying")
            rows += [line.partition("\t")[::2] for line in listed.stdout.splitlines() if line]
        root = Path(releases).resolve()
        return [
            name
            for name, files in rows
            if not files or not all(Path(f).resolve().is_relative_to(root) for f in files.split(","))
        ]

    def run(self, argv):
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"command failed ({result.returncode}): {result.stderr.strip()}")

    def can_snapshot(self):
        # The owner's backup command has fixed owner paths and credentials. Its presence on PATH
        # does not make it safe for a locally generated site.
        return False

    def pull(self, release_dir, services, tier="app"):
        if tier == "app":
            return super().pull(release_dir, services)
        if services:
            self.run([*edge_compose(release_dir), "pull", *services])

    def network_exists(self, name):
        return subprocess.run(["docker", "network", "inspect", name], capture_output=True).returncode == 0

    def network_ours(self, name):
        """Whether local setup created this network: it carries our label, which a same-named network would not."""
        argv = ["docker", "network", "inspect", "-f", '{{index .Labels "homelab.setup"}}', name]
        return subprocess.run(argv, capture_output=True, text=True).stdout.strip() == "local"

    def create_network(self, name):
        """Docker picks a subnet from its address pools that no route or other network uses, as for any network."""
        self.run(["docker", "network", "create", "--label", "homelab.setup=local", name])

    def wait_healthy(self, project, service, timeout_s):
        deadline = time.monotonic() + timeout_s
        while True:
            verdict = judge(self.inspect(service, project), self.settle_s, datetime.now(UTC))
            if verdict.status == "ok":
                return verdict
            if verdict.status == "fail" or time.monotonic() >= deadline:
                return Verdict("fail", f"{service}: {verdict.reason}")
            time.sleep(1)
