"""Compare a release's Compose containers with the live stack."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any


DROP = {"Id", "Created", "State", "Name", "Image", "Config.Hostname", "HostnamePath",
        "HostsPath", "ResolvConfPath", "LogPath", "GraphDriver", "RestartCount", "Platform"}


def _normal(value: Any, path: str, legacy: Path) -> Any:
    if not path and isinstance(value, dict):
        value = json.loads(json.dumps(value))
        ignored = {value.get("Id"), value.get("Name"), str(value.get("Name", "")).lstrip("/")}
        for network in (value.get("NetworkSettings", {}).get("Networks", {}) or {}).values():
            network["Aliases"] = [alias for alias in network.get("Aliases", ()) if alias not in ignored]
    if path == "Config.Env":
        return {item.split("=", 1)[0]: hashlib.sha256(item.split("=", 1)[1].encode()).hexdigest()[:12]
                for item in value if "=" in item}
    if path == "NetworkSettings":
        return {"Networks": {name: {"Aliases": sorted(set(data.get("Aliases", ())))}
                              for name, data in (value.get("Networks") or {}).items()}}
    if path.endswith(".Labels"):
        return {key: val for key, val in (value or {}).items() if not key.startswith("com.docker.compose.")}
    if path in ("Mounts", ".Mounts") and isinstance(value, list):
        value = [{key: val for key, val in mount.items() if key != "Name"} for mount in value]
    if isinstance(value, dict):
        return {key: _normal(val, f"{path}.{key}" if path else key, legacy)
                for key, val in value.items() if f"{path}.{key}" not in DROP and key not in DROP}
    if isinstance(value, list):
        return [_normal(item, path, legacy) for item in value]
    prefix = str(legacy).rstrip("/") + "/"
    if path.endswith(".Source") and isinstance(value, str) and value.startswith(prefix):
        return "/srv/homelab/data/" + value[len(prefix):]
    return value


def _diff(left: Any, right: Any, path: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        return sum((_diff(left.get(key), right.get(key), f"{path}.{key}".strip("."))
                    for key in sorted(set(left) | set(right))), [])
    if left != right:
        return [f"{path}: {json.dumps(left, sort_keys=True)} → {json.dumps(right, sort_keys=True)}"]
    return []


class Docker:
    def run(self, args: list[str]) -> None:
        subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    def ids(self, project: str, service: str) -> list[str]:
        return subprocess.check_output(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}",
                                        "--filter", f"label=com.docker.compose.service={service}"], text=True).split()
    def inspect(self, identifier: str) -> dict[str, Any]:
        return json.loads(subprocess.check_output(["docker", "inspect", identifier], text=True))[0]


def shadow(release: Path, legacy: Path, services: list[str] | None = None, adapter: Any = None) -> int:
    adapter = adapter or Docker()
    models = {name: json.loads((release / f"{name}.json").read_bytes()) for name in ("app", "edge")}
    names = services or sorted(set().union(*(model.get("services", {}) for model in models.values())))
    result = 0
    for service in names:
        model = next(model for model in models.values() if service in model.get("services", {}))
        running_ids = adapter.ids("homelab", service)
        running = adapter.inspect(running_ids[0]) if running_ids else None
        definition = json.loads(json.dumps(model))
        definition["services"][service].pop("container_name", None)
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:  # releases are read-only
            json.dump(definition, handle); handle.flush()
            try:
                adapter.run(["docker", "compose", "-p", "homelab-shadow", "-f", handle.name,
                             "create", "--no-deps", "--pull", "missing", service])
                candidate = adapter.inspect(adapter.ids("homelab-shadow", service)[0])
            finally:
                adapter.run(["docker", "compose", "-p", "homelab-shadow", "-f", handle.name, "rm", "-f", service])
        if running is None:
            print(f"{service}: not running"); result = 1; continue
        differences = _diff(_normal(running, "", legacy), _normal(candidate, "", legacy))
        if differences:
            result = 1; print("\n".join(differences))
        else:
            print(f"{service}: clean")
    return result
