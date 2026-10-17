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

    def compose_version(self) -> str: ...

    def path_exists(self, path: Path | str) -> bool: ...


@dataclass(frozen=True)
class Plan:
    recreate: tuple[str, ...]
    create_stopped: tuple[str, ...]
    notices: tuple[str, ...]
    failures: tuple[str, ...]
    hold: str | None


def _document(path: Path) -> Mapping[str, object]:
    if path.suffix == ".yaml":
        value = yaml.safe_load(path.read_bytes())
    else:
        value = json.loads(path.read_bytes())
    return value


def _marker_paths(model: Mapping[str, object]) -> tuple[str, ...]:
    paths: set[str] = set()
    services = model.get("services", {})
    for service in services.values():
        for volume in service.get("volumes", ()):
            if isinstance(volume, Mapping) and volume.get("type") == "bind":
                source = volume.get("source")
                if isinstance(source, str) and source.endswith("/.homelab-mounted"):
                    paths.add(source)
    return tuple(sorted(paths))


def plan(release_dir: Path | str, adapter: DockerAdapter,
         recorded_compose_version: str | None) -> Plan:
    root = Path(release_dir)
    app = _document(root / "app.json")
    edge = _document(root / "edge.json")
    site = _document(root / "site.yaml")
    app_services = app.get("services", {})
    edge_services = edge.get("services", {})
    managed = set(site.get("managed", ()))
    site_services = site.get("services", {})

    failures = [f"media disk missing: {path}" for path in _marker_paths(app)
                if not adapter.path_exists(path)]
    recreate: list[str] = []
    create_stopped: list[str] = []
    notices: list[str] = []

    for service in sorted(app_services):
        if service not in managed:
            continue
        desired = adapter.config_hash(root, "homelab", service)
        current = adapter.container("homelab", service)
        state = site_services.get(service, {}).get("state", "running")
        if current is not None and current.error:
            failures.append(f"{service} failed to start: {current.error}")
            continue
        if current is not None and current.status == "exited" and state == "running":
            notices.append(f"stopped by owner: {service}")
            continue
        changed = current is None or desired != current.config_hash
        if changed:
            (create_stopped if state == "stopped" else recreate).append(service)

    for service in sorted(edge_services):
        desired = adapter.config_hash(root, "homelab", service)
        current = adapter.container("homelab", service)
        if current is None or desired != current.config_hash:
            notices.append(f"edge change pending: {service}")

    hold = None
    if recorded_compose_version is not None and adapter.compose_version() != recorded_compose_version:
        hold = "Compose changed: check homelabctl status, then reconcile --now"
    return Plan(tuple(recreate), tuple(create_stopped), tuple(notices), tuple(failures), hold)


def compose_commands(p: Plan, release_dir: Path | str) -> list[list[str]]:
    compose = str(Path(release_dir) / "app.json")
    commands: list[list[str]] = []
    if p.recreate:
        commands.append(["docker", "compose", "-p", "homelab", "-f", compose, "up", "-d",
                         "--no-deps", "--no-build", "--pull", "never", *p.recreate])
    if p.create_stopped:
        commands.append(["docker", "compose", "-p", "homelab", "-f", compose, "up",
                         "--no-deps", "--no-start", *p.create_stopped])
    return commands
