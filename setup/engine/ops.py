"""Docker effects for local apply, built on the controller's adapter."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.controller.reconcile import SubprocessOps

NETWORK = "homelab_net"


def unhealthy(inspected: dict | None) -> str:
    """Why a container is not serving, or "" when it runs, has not restarted, and passes any healthcheck it has."""
    if inspected is None:
        return "no container"
    state = inspected.get("State", {})
    if state.get("Status") != "running":
        return f"container is {state.get('Status') or 'unknown'}"
    if inspected.get("RestartCount", 0):
        return "restarting"
    health = state.get("Health", {}).get("Status")
    return "" if health in (None, "healthy") else f"health check is {health}"


def edge_compose(release_dir) -> list[str]:
    """`docker compose` for the edge tier of a release. The app tier goes through the controller's planner."""
    return ["docker", "compose", "-p", "homelab-edge", "-f", str(Path(release_dir) / "edge.json")]


class LocalOps(SubprocessOps):
    def __init__(self):
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

    def foreign_projects(self, releases: Path) -> list[str]:
        """The owner's Compose projects (homelab, homelab-edge) whose Compose files are not under our releases."""
        listed = subprocess.run(["docker", "compose", "ls", "-a", "--format", "json"], capture_output=True, text=True)
        if listed.returncode != 0:
            raise RuntimeError("cannot inspect Docker ownership; restore Docker access before applying")
        root = Path(releases).resolve()
        owner = [p for p in json.loads(listed.stdout or "[]") if p["Name"] in ("homelab", "homelab-edge")]
        return [
            p["Name"]
            for p in owner
            if not all(Path(f).resolve().is_relative_to(root) for f in p["ConfigFiles"].split(","))
        ]

    def run(self, argv):
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"command failed ({result.returncode}): {result.stderr.strip()}")

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
