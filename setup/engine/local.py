"""Local apply: turn a rendered site into a release and the ordered steps that start it.

  Release     the app and edge Compose models plus site.yaml, stored in a directory named by their content hash.
  build_steps the steps `homelab setup` runs, in order: write the release, create the network, make data
              directories, generate secrets, then start services phase by phase (see config/phases.json).

Not signed, so it never uses the controller's `materialize()` or `reconcile()`. It reads and writes only the
person's own site, state and data directories.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from scripts.release.render import render

from .contract import Context, Step
from .layout import Layout
from .steps import Dirs, Network, Phase, Secrets, WriteRelease

CONFIG = Path(__file__).parents[1] / "config" / "phases.json"
FILES = ("app.json", "edge.json", "site.yaml")


@dataclass(frozen=True)
class Release:
    """The two Compose models and site.yaml for one apply. The directory name is a hash of their content."""

    directory: Path
    app: dict[str, Any]
    edge: dict[str, Any]
    site: dict[str, Any]

    @classmethod
    def from_models(cls, layout: Layout, app: dict, edge: dict, site: dict) -> Release:
        site = {
            **site,
            "managed": site.get("managed", sorted({*app.get("services", {}), *edge.get("services", {})})),
        }
        blob = json.dumps([app, edge, site], sort_keys=True, separators=(",", ":"))
        return cls(
            layout.releases / hashlib.sha256(blob.encode()).hexdigest()[:16],
            app,
            edge,
            site,
        )

    @classmethod
    def render(
        cls,
        layout: Layout,
        platform: Path,
        site_dir: Path,
        plugins: Mapping[str, Path],
        network_spec: Mapping[str, str] | None = None,
    ) -> Release:
        rendered = render(platform, site_dir, plugins)
        site = yaml.safe_load((Path(site_dir) / "site.yaml").read_text(encoding="utf-8"))
        if network_spec is not None:
            caddy = rendered.edge.get("services", {}).get("caddy")
            if caddy is not None:
                caddy["networks"] = {"homelab_net": {"ipv4_address": network_spec["caddy_address"]}}
        return cls.from_models(layout, rendered.app, rendered.edge, site)

    def tier(self, service: str) -> str:
        return "app" if service in self.app.get("services", {}) else "edge"

    def require_loopback_published_ports(self) -> None:
        """Incomplete-preview guard: only explicitly numeric loopback binds may publish host ports."""
        ports = (
            (tier, service, port)
            for tier, model in (("app", self.app), ("edge", self.edge))
            for service, defn in model.get("services", {}).items()
            for port in defn.get("ports", ())
        )
        for tier, service, port in ports:
            host_ip = port.get("host_ip") if isinstance(port, Mapping) else None
            try:
                address = ipaddress.ip_address(host_ip)
            except (ValueError, TypeError):
                raise ValueError(
                    f"{tier} service {service} publishes a port without an explicit numeric loopback bind"
                ) from None
            if str(address) not in ("127.0.0.1", "::1"):
                raise ValueError(f"{tier} service {service} publishes a port outside loopback")

    def complete(self) -> bool:
        return all((self.directory / name).is_file() for name in FILES)

    def write(self) -> None:
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        contents = {
            "app.json": json.dumps(self.app, sort_keys=True),
            "edge.json": json.dumps(self.edge, sort_keys=True),
            "site.yaml": yaml.safe_dump(self.site, sort_keys=True),
        }
        staging = Path(tempfile.mkdtemp(prefix=".release.", dir=self.directory.parent))
        for name, text in contents.items():
            (staging / name).write_text(text + "\n", encoding="utf-8")
            os.chmod(staging / name, 0o444)
        os.chmod(staging, 0o555)
        if self.directory.exists():  # an incomplete leftover; the staged copy is whole
            os.chmod(self.directory, 0o755)
            for leftover in self.directory.iterdir():
                leftover.unlink()
            self.directory.rmdir()
        os.rename(staging, self.directory)


def defaults() -> tuple[Sequence[tuple[str, Sequence[str]]], Sequence[str]]:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    return [(name, services) for name, services in config["phases"]], config["generated_secrets"]


def _phases(release: Release, phases: Iterable[tuple[str, Sequence[str]]]) -> list[tuple[str, tuple[str, ...]]]:
    """Keep only services this site runs and manages; `*` takes every other one, so nothing is left unstarted."""
    models = {**release.app.get("services", {}), **release.edge.get("services", {})}
    present = set(models) & set(release.site["managed"])
    named = {service for _, services in phases for service in services}
    done: set[str] = set()
    ordered = []
    for name, services in phases:
        wanted = sorted(present - named) if "*" in services else [s for s in services if s in present]
        if wanted:
            ordered.append((name, _start_order(wanted, models, done)))
    return ordered


def _start_order(services: list[str], models: Mapping[str, Any], done: set[str]) -> tuple[str, ...]:
    """Order one phase so each service follows its depends_on; `done` collects everything already placed."""
    pending, result = list(services), []
    while pending:
        ready = next((s for s in pending if set(models[s].get("depends_on", ())) <= done), None)
        if ready is None:
            raise ValueError(f"dependencies missing, cyclic or in a later phase: {pending}")
        pending.remove(ready)
        result.append(ready)
        done.add(ready)
    return tuple(result)


def build_steps(
    ctx: Context,
    release: Release,
    ops: Any,
    *,
    phases: Iterable[tuple[str, Sequence[str]]] | None = None,
    generated: Sequence[str] | None = None,
    extra: Sequence[Step] = (),
    requires: Mapping[str, Sequence[str]] | None = None,
    network_spec: Mapping[str, str] | None = None,
) -> list[Step]:
    """The core steps plus any `extra` ones. `requires` makes a core step wait for an extra one, by id.

    Core ids: engine.release, engine.network, engine.dirs, engine.secrets, then phase.<name> in order. A provider
    step usually needs `engine.release` and is listed in `requires` for the phase that consumes what it makes.
    """
    default_phases, default_generated = defaults()
    chosen = _phases(release, default_phases if phases is None else phases)
    foundation = ("engine.network", "engine.dirs", "engine.secrets")
    steps: list[Step] = [
        WriteRelease(release),
        Network(ops, network_spec),
        Dirs(release),
        Secrets(release, tuple(default_generated if generated is None else generated), ops),
    ]
    previous: tuple[str, ...] = foundation
    for name, services in chosen:
        steps.append(Phase(f"phase.{name}", previous, release, services, ops))
        previous = (f"phase.{name}",)
    wanted = dict(requires or {})
    if unknown := set(wanted) - {s.id for s in steps}:
        raise ValueError(f"requires names unknown steps: {sorted(unknown)}")
    for name, services in chosen:
        if "cloudflared" in services:
            wanted[f"phase.{name}"] = (*wanted.get(f"phase.{name}", ()), "GATE-1")
    steps = [replace(s, needs=(*s.needs, *wanted[s.id])) if s.id in wanted else s for s in steps]
    return [*steps, *extra]
