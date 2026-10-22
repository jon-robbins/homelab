"""Pure planning for a materialized release."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping, Protocol

import yaml


@dataclass(frozen=True)
class ContainerView:
    config_hash: str | None
    status: str
    error: str


class DockerAdapter(Protocol):
    def config_hash(self, release_dir: Path | str, project: str, service: str) -> str: ...

    def container(self, project: str, service: str) -> ContainerView | None: ...

    def path_exists(self, path: Path | str) -> bool: ...


@dataclass(frozen=True)
class Plan:
    recreate: tuple[str, ...]
    create_stopped: tuple[str, ...]
    notices: tuple[str, ...]
    failures: tuple[str, ...]


def _document(path: Path) -> Mapping[str, object]:
    return yaml.safe_load(path.read_bytes()) if path.suffix == ".yaml" else json.loads(path.read_bytes())


def _marker_paths(model: Mapping[str, object]) -> tuple[str, ...]:
    sources = {
        volume.get("source")
        for service in model.get("services", {}).values()
        for volume in service.get("volumes", ())
        if isinstance(volume, Mapping) and volume.get("type") == "bind"
    }
    return tuple(sorted(s for s in sources if isinstance(s, str) and s.endswith("/.homelab-mounted")))


def _missing_data_dirs(model: Mapping[str, object], managed: set[str], adapter: DockerAdapter) -> list[str]:
    # A stateful service is snapshotted from its data dir before every recreate, so that dir must already exist.
    return [
        f"data dir missing: {name}: {volume['source']}"
        for name, service in sorted(model.get("services", {}).items())
        if name in managed and (service.get("labels") or {}).get("homelab.state") == "stateful"
        for volume in service.get("volumes", ())
        if isinstance(volume, Mapping)
        and volume.get("type") == "bind"
        and not str(volume.get("source", "")).endswith("/.homelab-mounted")
        and not adapter.path_exists(volume["source"])
    ]


def plan(release_dir: Path | str, adapter: DockerAdapter) -> Plan:
    root = Path(release_dir)
    app, edge, site = (_document(root / name) for name in ("app.json", "edge.json", "site.yaml"))
    managed = set(site.get("managed", ()))
    site_services = site.get("services", {})

    failures = [f"media disk missing: {path}" for path in _marker_paths(app) if not adapter.path_exists(path)]
    failures += _missing_data_dirs(app, managed, adapter)
    recreate: list[str] = []
    create_stopped: list[str] = []
    notices: list[str] = []

    for service in sorted(set(app.get("services", {})) & managed):
        desired = adapter.config_hash(root, "homelab", service)
        current = adapter.container("homelab", service)
        state = site_services.get(service, {}).get("state", "running")
        if current is not None and current.error:
            failures.append(f"{service} failed to start: {current.error}")
        elif current is not None and current.status == "exited" and state == "running":
            notices.append(f"stopped by owner: {service}")
        elif current is None or desired != current.config_hash:
            (create_stopped if state == "stopped" else recreate).append(service)

    for service in sorted(edge.get("services", {})):
        current = adapter.container("homelab-edge", service)
        if current is None or adapter.config_hash(root, "homelab-edge", service) != current.config_hash:
            notices.append(f"edge change pending: {service}")

    return Plan(tuple(recreate), tuple(create_stopped), tuple(notices), tuple(failures))


def compose_commands(p: Plan, release_dir: Path | str) -> list[list[str]]:
    compose = ["docker", "compose", "-p", "homelab", "-f", str(Path(release_dir) / "app.json")]
    commands = [[*compose, "up", "-d", "--no-deps", "--no-build", "--pull", "never", *p.recreate]] if p.recreate else []
    if p.create_stopped:
        commands.append([*compose, "up", "--no-deps", "--no-start", *p.create_stopped])
    return commands
