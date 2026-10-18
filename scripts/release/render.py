"""Render a site into the two Compose models (app.json, edge.json).

Pipeline:
1. load_site validates site.yaml, site.env and every plugin; each enabled service gets exactly one module.
2. Each module (and its site override) is copied to a temp dir with config file paths made absolute.
3. `docker compose config --format json` runs with an empty environment: Compose merges the overrides,
   resolves ${NAME} from site.env and normalizes every short form.
4. On that normalized model: config files are inlined (content and mode), disk markers are added (D9).
5. compose_policy checks the result.

    python3 -m scripts.release.render <site-dir> [--plugin NAME=DIR]...   # from the homelab repo root
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Mapping

import yaml

from .compose_policy import Context, check_policy, under
from .contracts import ValidationError
from .modules import Site, check_module, find_module, load_site, load_yaml


@dataclass(frozen=True)
class Rendered:
    app: dict[str, Any]
    edge: dict[str, Any]


@dataclass(frozen=True)
class _Service:
    name: str
    tier: str
    module: Path
    owner: Path  # repo root the module's relative config paths resolve against
    namespace: str | None  # image namespace of the code plugin it comes from, if any


def _select(name: str, spec: Mapping[str, Any], site: Site, platform: Path) -> _Service:
    plugin = spec.get("from")
    if plugin is not None and plugin not in site.plugins:
        raise ValidationError(f"site.yaml.services.{name}.from", f"unknown plugin {plugin}")
    owner = site.plugins[plugin].root if plugin else platform
    module = find_module(owner, name)
    if module is None:
        raise ValidationError(f"site.yaml.services.{name}", "no module for this service")
    namespace = site.plugins[plugin].image_namespace if plugin else None
    return _Service(name, check_module(module, site.env), module, owner, namespace)


def _prepared(source: Path, owner: Path, site: Path, directory: Path) -> Path:
    """Copy a module or override with config `file:` paths made absolute (`site:` = the site repo)."""
    document = load_yaml(source)
    for config in (document.get("configs") or {}).values():
        if "file" in config:
            reference = config["file"]
            config["file"] = str(site / reference[5:] if reference.startswith("site:") else owner / reference)
    target = directory / f"{len(list(directory.iterdir()))}-{source.name}"
    target.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return target


def _compose(files: list[Path], tier: str, site: Path, directory: Path) -> dict[str, Any]:
    if not files:
        return {}
    project = "homelab" if tier == "app" else "homelab-edge"
    command = ["docker", "compose", "-p", project, *(arg for file in files for arg in ("-f", str(file)))]
    command += ["--env-file", str(site / "site.env"), "config", "--format", "json"]
    result = subprocess.run(command, cwd=directory, env={"PATH": os.environ["PATH"]}, capture_output=True, text=True)
    if result.returncode:
        raise ValidationError("$", f"docker compose config failed: {result.stderr.strip()}")
    model = json.loads(result.stdout)
    for service in (model.get("services") or {}).values():
        for volume in service.get("volumes") or []:
            if volume.get("type") == "bind" and not volume.get("bind"):
                volume["bind"] = {"create_host_path": False}
    return model


def _inline_configs(model: dict[str, Any]) -> None:
    """A release must not point back into a git checkout (D15): config files become inline content."""
    modes = {}
    for name, config in (model.get("configs") or {}).items():
        if "file" in config:
            path = Path(config.pop("file"))
            config["content"] = path.read_text(encoding="utf-8")
            modes[name] = path.stat().st_mode & 0o777
    for service in (model.get("services") or {}).values():
        for entry in service.get("configs") or []:
            if entry.get("source") in modes:
                entry.setdefault("mode", modes[entry["source"]])


def _add_markers(model: dict[str, Any], site: Site) -> None:
    """D9: a service that binds under a media root also binds that root's marker, so it can't start without the disk."""
    roots = {key: site.env[key] for key in site.config.get("marker_roots", [])}
    for service in (model.get("services") or {}).values():
        volumes = service.setdefault("volumes", [])
        sources = [volume["source"] for volume in volumes if volume.get("type") == "bind"]
        for key, root in roots.items():
            marker = os.path.join(root, ".homelab-mounted")
            if marker not in sources and any(under(source, root) for source in sources):
                volumes.append(
                    {
                        "type": "bind",
                        "source": marker,
                        "target": f"/homelab-markers/{key}",
                        "read_only": True,
                        "bind": {"create_host_path": False},
                    }
                )


def _add_logging_defaults(model: dict[str, Any]) -> None:
    for service in (model.get("services") or {}).values():
        service.setdefault(
            "logging",
            {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
        )


def render(platform: Path, site_dir: Path, plugins: Mapping[str, Path]) -> Rendered:
    platform = Path(platform).resolve()
    site = load_site(site_dir, platform, plugins)
    missing = [key for key in site.config["bind_roots"] if key not in site.env]
    if missing:
        raise ValidationError("site.env", f"missing bind root keys {missing}")
    services = [_select(name, spec or {}, site, platform) for name, spec in site.config["services"].items()]
    with tempfile.TemporaryDirectory(prefix="homelab-render-") as temporary:
        directory = Path(temporary)
        files: dict[str, list[Path]] = {"app": [], "edge": []}
        for service in services:
            override = site.root / "overrides" / f"{service.name}.yml"
            files[service.tier].append(_prepared(service.module, service.owner, site.root, directory))
            if override.exists():
                files[service.tier].append(_prepared(override, site.root, site.root, directory))
        models = {tier: _compose(paths, tier, site.root, directory) for tier, paths in files.items()}
    ctx = Context(
        tiers={service.name: service.tier for service in services},
        namespaces={service.name: service.namespace for service in services},
        env=site.env,
        roots=tuple(site.env[key] for key in site.config["bind_roots"]),
    )
    for model in models.values():
        if model:
            _inline_configs(model)
            _add_markers(model, site)
            _add_logging_defaults(model)
            check_policy(model, ctx)
    return Rendered(models["app"], models["edge"])


def _plugin(value: str) -> tuple[str, Path]:
    name, _, folder = value.partition("=")
    if not name or not folder:
        raise argparse.ArgumentTypeError("--plugin must be NAME=DIR")
    return name, Path(folder)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m scripts.release.render", description="Render and validate a site."
    )
    parser.add_argument("site", type=Path)
    parser.add_argument("--plugin", type=_plugin, action="append", default=[], help="NAME=DIR, once per plugin")
    args = parser.parse_args(argv)
    try:
        result = render(Path.cwd(), args.site, dict(args.plugin))
    except (ValidationError, OSError) as error:
        print(f"render failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"app": result.app, "edge": result.edge}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
