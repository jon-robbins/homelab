"""Local apply: a rendered site becomes a content-addressed release directory, then ordered, health-checked phases.

Never uses the controller's `materialize()` or `reconcile()`: those assume a signed release was verified, and
nothing here is signed. The steps read and write only paths under the person's own site, state and data directories.
"""

from __future__ import annotations

import hashlib
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
    managed = set(release.site["managed"])
    present = (set(release.app.get("services", {})) | set(release.edge.get("services", {}))) & managed
    named = {service for _, services in phases for service in services}
    chosen = []
    for name, services in phases:
        picked = sorted(present - named) if "*" in services else [s for s in services if s in present]
        if picked:
            chosen.append((name, tuple(picked)))
    models = {**release.app.get("services", {}), **release.edge.get("services", {})}
    done = set()
    ordered = []
    for name, services in chosen:
        pending, result = list(services), []
        while pending:
            ready = next(
                (s for s in pending if set(models[s].get("depends_on", ())) <= done),
                None,
            )
            if ready is None:
                raise ValueError(f"dependencies missing, cyclic or in a later phase: {pending}")
            pending.remove(ready)
            result.append(ready)
            done.add(ready)
        ordered.append((name, tuple(result)))
    return ordered


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
