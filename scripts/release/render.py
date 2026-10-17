"""Render a site catalog into the two Compose models."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping

import yaml

from .compose_policy import check_policy
from .contracts import ValidationError
from .modules import validate_module, validate_site


@dataclass(frozen=True)
class Rendered:
    app: dict[str, Any]
    edge: dict[str, Any]


def _env(site: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    path = site / "site.env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    return values


def _under(path: str, root: str) -> bool:
    candidate = Path(path).expanduser().resolve(strict=False)
    parent = Path(root).expanduser().resolve(strict=False)
    return candidate == parent or parent in candidate.parents


def _inline(document: dict[str, Any], owner: Path, site: Path) -> dict[str, int]:
    configs = document.get("configs", {})
    modes: dict[str, int] = {}
    if not isinstance(configs, dict):
        return modes
    for name, config in configs.items():
        if not isinstance(config, dict) or "file" not in config:
            continue
        reference = config["file"]
        source = site / reference.removeprefix("site:") if isinstance(reference, str) and reference.startswith("site:") else owner / reference
        config.pop("file")
        config["content"] = source.read_text(encoding="utf-8")
        modes[name] = source.stat().st_mode & 0o777
    return modes


def _config_modes(document: dict[str, Any], modes: Mapping[str, int]) -> None:
    for service in (document.get("services") or {}).values():
        entries = service.get("configs", []) if isinstance(service, dict) else []
        for index, entry in enumerate(entries):
            if isinstance(entry, dict) and entry.get("source") in modes and "mode" not in entry:
                entry["mode"] = modes[entry["source"]]
            elif isinstance(entry, str):
                parts = entry.split(":", 2)
                if len(parts) == 2 and parts[0] in modes:
                    entries[index] = {"source": parts[0], "target": parts[1], "mode": modes[parts[0]]}
                elif entry in modes:
                    entries[index] = {"source": entry, "target": f"/{entry}", "mode": modes[entry]}


def _volume_source(volume: Any) -> str | None:
    if isinstance(volume, dict):
        if volume.get("type") == "bind":
            source = volume.get("source", volume.get("src"))
            return source if isinstance(source, str) else None
    elif isinstance(volume, str):
        parts = volume.split(":")
        if len(parts) >= 2 and parts[0].startswith(("/", ".", "~", "${")):
            return parts[0]
    return None


def _add_markers(document: dict[str, Any], effective: dict[str, list[Any]],
                 env: Mapping[str, str], roots: list[str]) -> None:
    for name, service in (document.get("services") or {}).items():
        if not isinstance(service, dict):
            continue
        volumes = service.setdefault("volumes", [])
        existing = {(v.get("source", v.get("src")), v.get("target")) for v in volumes if isinstance(v, dict)}
        existing.update((parts[0], parts[1]) for v in volumes if isinstance(v, str)
                        for parts in [v.split(":", 2)] if len(parts) >= 2)
        for key in roots:
            found = False
            for volume in effective.get(name, []):
                source = _volume_source(volume)
                if source is not None:
                    expanded = source
                    for env_key, value in env.items():
                        expanded = expanded.replace("${" + env_key + "}", value)
                    if _under(expanded, env[key]):
                        found = True
                        break
            marker = str(Path(env[key]) / ".homelab-mounted")
            target = f"/homelab-markers/{key}"
            if found and (marker, target) not in existing:
                volumes.append({"type": "bind", "source": marker, "target": target,
                                "read_only": True, "bind": {"create_host_path": False}})
                existing.add((marker, target))


def _compose(files: list[Path], tier: str, site: Path, temporary: Path) -> dict[str, Any]:
    if not files:
        return {}
    command = ["docker", "compose", "-p", "homelab" if tier == "app" else "homelab-edge"]
    for file in files:
        command += ["-f", str(file)]
    command += ["--env-file", str(site / "site.env"), "config", "--format", "json"]
    result = subprocess.run(command, cwd=temporary, env={"PATH": os.environ["PATH"]},
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValidationError("$", f"docker compose config failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def render(platform: Path, site: Path, plugins: dict[str, Path]) -> Rendered:
    site = site.resolve()
    document = validate_site(site, platform, plugins)
    site_env = _env(site)
    marker_roots = document.get("marker_roots", [])
    tiers: dict[str, str] = {}
    selected: list[tuple[str, Path, Path]] = []
    for name, service_spec in document.get("services", {}).items():
        source_name = service_spec.get("from") if isinstance(service_spec, dict) else None
        if source_name and source_name not in plugins:
            raise ValidationError(f"$.services.{name}.from", "unknown plugin")
        owner = plugins[source_name] if source_name else platform
        if source_name:
            platform_matches = [platform / "modules" / tier / f"{name}.yml" for tier in ("app", "edge")
                                if (platform / "modules" / tier / f"{name}.yml").exists()]
            plugin_matches = [owner / "modules" / tier / f"{name}.yml" for tier in ("app", "edge")
                              if (owner / "modules" / tier / f"{name}.yml").exists()]
            if platform_matches and plugin_matches:
                raise ValidationError(f"$.services.{name}", "plugin service collides with platform module")
        candidates = [owner / "modules" / tier / f"{name}.yml" for tier in ("app", "edge")]
        matches = [path for path in candidates if path.exists()]
        if len(matches) != 1:
            raise ValidationError(f"$.services.{name}", "service must have exactly one module")
        module = matches[0]
        tier = module.parent.name
        tiers[name] = tier
        validate_module(module, site_env, document["bind_roots"])
        selected.append((name, module, owner))

    with tempfile.TemporaryDirectory(prefix="homelab-render-") as directory:
        temporary = Path(directory)
        compose_files: dict[str, list[Path]] = {"app": [], "edge": []}
        for name, module, owner in selected:
            transformed = yaml.safe_load(module.read_text(encoding="utf-8"))
            modes = _inline(transformed, owner, site)
            override = site / "overrides" / f"{name}.yml"
            override_doc = yaml.safe_load(override.read_text(encoding="utf-8")) if override.exists() else None
            if override_doc is not None:
                modes.update(_inline(override_doc, site, site))
            _config_modes(transformed, modes)
            if override_doc is not None:
                _config_modes(override_doc, modes)
            effective = {name: list(transformed["services"][name].get("volumes", []))}
            if override_doc is not None:
                for service_name, service in (override_doc.get("services") or {}).items():
                    by_target = {v.get("target") if isinstance(v, dict) else v.split(":", 2)[1]: v
                                 for v in effective.get(service_name, [])}
                    for volume in service.get("volumes", []):
                        target = volume.get("target") if isinstance(volume, dict) else volume.split(":", 2)[1]
                        by_target[target] = volume
                    effective[service_name] = list(by_target.values())
            marker_doc = (override_doc if override_doc is not None
                          and name in (override_doc.get("services") or {}) else transformed)
            _add_markers(marker_doc, effective, site_env, marker_roots)
            output = temporary / f"{module.parent.name}-{name}.yml"
            output.write_text(yaml.safe_dump(transformed, sort_keys=False), encoding="utf-8")
            compose_files[module.parent.name].append(output)
            if override_doc is not None:
                override_output = temporary / f"{module.parent.name}-{name}-override.yml"
                override_output.write_text(yaml.safe_dump(override_doc, sort_keys=False), encoding="utf-8")
                compose_files[module.parent.name].append(override_output)
        models = {tier: _compose(files, tier, site, temporary) for tier, files in compose_files.items()}
    for tier, model in models.items():
        if not model:
            continue
        check_policy(model, tiers=tiers, site_env=site_env, bind_root_keys=document["bind_roots"])
    return Rendered(models["app"], models["edge"])
