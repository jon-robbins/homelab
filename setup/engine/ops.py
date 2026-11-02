"""Docker effects for local apply, built on the controller's adapter, with health judged strictly."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
import ipaddress
import json
from pathlib import Path
import secrets

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


def _random_private_network():
    """A random /16 from one of the three RFC 1918 blocks."""
    prefixes = [(10, secrets.randbelow(256)), (172, 16 + secrets.randbelow(16)), (192, 168)]
    first, second = secrets.choice(prefixes)
    return ipaddress.ip_network((first << 24 | second << 16, 16))


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
            model = str(Path(release_dir) / "edge.json")
            self.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    "homelab-edge",
                    "-f",
                    model,
                    "pull",
                    *services,
                ]
            )

    def network_exists(self, name):
        return subprocess.run(["docker", "network", "inspect", name], capture_output=True).returncode == 0

    def _taken_subnets(self):
        """Every subnet already routed on this host or used by a Docker network."""
        routes = subprocess.run(["ip", "-j", "route"], capture_output=True, text=True, check=True)
        subnets = [r["dst"] for r in json.loads(routes.stdout) if r.get("dst") not in (None, "default")]
        listed = subprocess.run(["docker", "network", "ls", "-q"], capture_output=True, text=True, check=True)
        if listed.stdout.strip():
            argv = ["docker", "network", "inspect", *listed.stdout.split()]
            inspected = subprocess.run(argv, capture_output=True, text=True, check=True)
            for network in json.loads(inspected.stdout):
                subnets += [c["Subnet"] for c in network.get("IPAM", {}).get("Config") or () if c.get("Subnet")]
        return [ipaddress.ip_network(s) for s in subnets]

    def _network_conflicts(self, candidate):
        return any(candidate.overlaps(taken) for taken in self._taken_subnets())

    def plan_network(self, state_dir):
        """The saved network plan, choosing and saving one first if none exists. Apply calls this before rendering."""
        spec = self.network_spec(state_dir)
        self._save_network_plan(Path(state_dir) / "network.json", spec)
        return spec

    def network_spec(self, state_dir):
        path = Path(state_dir) / "network.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        for _ in range(256):
            candidate = _random_private_network()
            if self._network_conflicts(candidate):
                continue
            return {
                "subnet": str(candidate),
                "gateway": str(candidate.network_address + 1),
                "caddy_address": str(candidate.network_address + 2),
                "ip_range": str(ipaddress.ip_network((int(candidate.network_address) + 256, 24))),
            }
        raise RuntimeError("could not find a non-conflicting private subnet")

    def network_ready(self, name, state_dir):
        path = Path(state_dir) / "network.json"
        if not path.exists() or not self.network_exists(name):
            return False
        expected = json.loads(path.read_text(encoding="utf-8"))
        inspected = subprocess.run(["docker", "network", "inspect", name], capture_output=True, text=True, check=True)
        configs = json.loads(inspected.stdout)[0].get("IPAM", {}).get("Config", ())
        return any(
            config.get("Subnet") == expected["subnet"]
            and config.get("Gateway") == expected["gateway"]
            and config.get("IPRange") == expected["ip_range"]
            for config in configs
        )

    def ensure_network(self, name, state_dir, spec):
        """Create the bridge network from the saved plan; refuse a same-named network we did not make."""
        if self.network_exists(name):
            if not self.network_ready(name, state_dir):
                raise RuntimeError(f"existing {name} network is not owned by local setup or does not match its saved plan")
            return
        self._save_network_plan(Path(state_dir) / "network.json", spec)
        self.run(
            ["docker", "network", "create", "--driver", "bridge", "--subnet", spec["subnet"]]
            + ["--gateway", spec["gateway"], "--ip-range", spec["ip_range"], name]
        )

    @staticmethod
    def _save_network_plan(path, spec):
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(spec, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def wait_healthy(self, project, service, timeout_s):
        deadline = time.monotonic() + timeout_s
        while True:
            verdict = judge(self.inspect(service, project), self.settle_s, datetime.now(UTC))
            if verdict.status == "ok":
                return verdict
            if verdict.status == "fail" or time.monotonic() >= deadline:
                return Verdict("fail", f"{service}: {verdict.reason}")
            time.sleep(1)
