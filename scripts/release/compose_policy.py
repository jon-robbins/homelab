"""Validation of the safe operational subset of a rendered Compose model."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .contracts import ValidationError, ValidationErrors


_VARIABLE = re.compile(r"\$\{([^{}]+)\}")
_FORBIDDEN_KEYS = {
    "build": "build is not permitted",
    "devices": "devices are not permitted",
    "cap_add": "cap_add is not permitted",
    "env_file": "env_file is not permitted",
    "post_start": "lifecycle hooks are not permitted",
    "pre_stop": "lifecycle hooks are not permitted",
}
_HOME_PREFIXES = (Path("/home"), Path("/root"), Path("/Users"))


_POLICY_PROTECTED = ("/srv/homelab", "/srv/vpn", "/run/docker.sock", "/var/run/docker.sock")
_RELEASE_CONFIG = Path(__file__).parents[2] / "config/release"


def _load_allowlist(name: str) -> dict[str, Any]:
    return json.loads((_RELEASE_CONFIG / name).read_bytes())


def _policy_overlap(first: str, second: str) -> bool:
    first = os.path.normpath(first)
    second = os.path.normpath(second)
    return first == second or first.startswith(second.rstrip("/") + "/") or second.startswith(first.rstrip("/") + "/")


def _policy_literal_under(path: str, parent: str) -> bool:
    path = os.path.normpath(path)
    parent = os.path.normpath(parent)
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def _policy_under(path: str, parent: str) -> bool:
    path = os.path.realpath(path)
    parent = os.path.realpath(parent)
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def _policy_device_source(value: Any) -> str | None:
    if isinstance(value, str):
        return value.split(":", 1)[0]
    if isinstance(value, Mapping) and isinstance(value.get("source"), str):
        return value["source"]
    return None


def _policy_gpu_request(value: Any, path: str, drivers: set[str], capabilities: set[str], errors: list[ValidationError]) -> None:
    if not isinstance(value, Mapping):
        errors.append(ValidationError(path, "GPU request must be an object"))
        return
    driver = value.get("driver")
    if driver not in (None, "") and (not isinstance(driver, str) or driver not in drivers):
        errors.append(ValidationError(f"{path}.driver", "GPU driver is not on the allowlist"))
    requested = value.get("capabilities", [])
    if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested) or not set(requested).issubset(capabilities):
        errors.append(ValidationError(f"{path}.capabilities", "GPU capability is not on the allowlist"))


def _policy_walk(value: Any, path: str, errors: list[ValidationError]) -> None:
    if isinstance(value, str):
        if "docker.sock" in value:
            errors.append(ValidationError(path, "docker.sock is not permitted"))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _policy_walk(item, f"{path}[{index}]", errors)
        return
    if not isinstance(value, Mapping):
        return
    for key, item in value.items():
        item_path = f"{path}.{key}"
        if key in {"build", "env_file", "post_start", "pre_stop", "include"}:
            errors.append(ValidationError(item_path, f"{key} is not permitted"))
        if key == "privileged" and item is True:
            errors.append(ValidationError(item_path, "privileged is not permitted"))
        _policy_walk(item, item_path, errors)


def check_policy(
    model: dict,
    *,
    tiers: Mapping[str, str],
    site_env: Mapping[str, str],
    bind_root_keys: Sequence[str],
) -> None:
    """Check the v2 policy against an already-rendered Compose model."""

    errors: list[ValidationError] = []
    capabilities = set(_load_allowlist("cap-allowlist.json").get("capabilities", ()))
    gpu = _load_allowlist("gpu-allowlist.json")
    gpu_devices = set(gpu.get("devices", ()))
    gpu_runtimes = set(gpu.get("runtimes", ()))
    gpu_drivers = set(gpu.get("drivers", ()))
    gpu_capabilities = set(gpu.get("capabilities", ()))

    roots: list[str] = []
    for key in bind_root_keys:
        if key not in site_env:
            errors.append(ValidationError(f"$.site_env.{key}", "missing bind root"))
            continue
        root = site_env[key]
        roots.append(root)
        if not os.path.isabs(root):
            errors.append(ValidationError(f"$.site_env.{key}", "bind root must be absolute"))
        else:
            for protected in _POLICY_PROTECTED:
                if protected == "/srv/homelab" and _policy_literal_under(root, "/srv/homelab/data"):
                    continue
                if _policy_overlap(root, protected) or _policy_overlap(os.path.realpath(root), os.path.realpath(protected)):
                    errors.append(ValidationError(f"$.site_env.{key}", f"bind root overlaps protected path {protected}"))
                    break

    services = model.get("services", {})
    if isinstance(services, Mapping):
        for name, service in services.items():
            service_path = f"$.services.{name}"
            tier = tiers.get(name)
            if tier not in {"app", "edge"}:
                errors.append(ValidationError(f"{service_path}.tier", "missing or invalid service tier"))
            if not isinstance(service, Mapping):
                continue
            network_mode = service.get("network_mode")
            if network_mode == "host" and tier != "edge":
                errors.append(ValidationError(f"{service_path}.network_mode", "host networking requires edge tier"))
            for key in ("pid", "ipc"):
                if service.get(key) == "host":
                    errors.append(ValidationError(f"{service_path}.{key}", "host mode is not permitted"))

            volumes = service.get("volumes", [])
            if isinstance(volumes, list):
                for index, volume in enumerate(volumes):
                    if not isinstance(volume, Mapping) or volume.get("type") != "bind":
                        continue
                    source = volume.get("source")
                    source_path = f"{service_path}.volumes[{index}].source"
                    if not isinstance(source, str) or not os.path.isabs(source):
                        errors.append(ValidationError(source_path, "bind source must be absolute"))
                    elif not any(_policy_under(source, root) for root in roots):
                        errors.append(ValidationError(source_path, "bind is outside declared site roots"))

            cap_add = service.get("cap_add", [])
            if isinstance(cap_add, list):
                for index, capability in enumerate(cap_add):
                    if not isinstance(capability, str) or capability.removeprefix("CAP_") not in capabilities:
                        errors.append(ValidationError(f"{service_path}.cap_add[{index}]", "capability is not on the allowlist"))

            if "runtime" in service and (not isinstance(service["runtime"], str) or service["runtime"] not in gpu_runtimes):
                errors.append(ValidationError(f"{service_path}.runtime", "runtime is not on the GPU allowlist"))
            devices = service.get("devices", [])
            if isinstance(devices, list):
                for index, device in enumerate(devices):
                    source = _policy_device_source(device)
                    if source is None or not any(_policy_literal_under(source, allowed) for allowed in gpu_devices):
                        errors.append(ValidationError(f"{service_path}.devices[{index}]", "device is not on the GPU allowlist"))
            if "gpus" in service:
                gpus = service["gpus"]
                if gpus != "all":
                    if isinstance(gpus, list):
                        for index, request in enumerate(gpus):
                            _policy_gpu_request(request, f"{service_path}.gpus[{index}]", gpu_drivers, gpu_capabilities, errors)
                    else:
                        errors.append(ValidationError(f"{service_path}.gpus", "invalid GPU value"))
            reservations = service.get("deploy", {})
            if isinstance(reservations, Mapping):
                reservations = reservations.get("resources", {})
            if isinstance(reservations, Mapping):
                reservations = reservations.get("reservations", {})
            if isinstance(reservations, Mapping):
                requests = reservations.get("devices", [])
                if isinstance(requests, list):
                    for index, request in enumerate(requests):
                        _policy_gpu_request(request, f"{service_path}.deploy.resources.reservations.devices[{index}]", gpu_drivers, gpu_capabilities, errors)

    secrets = model.get("secrets", {})
    secret_dir = site_env.get("HOMELAB_SECRET_DIR")
    if isinstance(secrets, Mapping):
        for name, secret in secrets.items():
            path = f"$.secrets.{name}"
            expected = os.path.normpath(f"{secret_dir}/{name}") if isinstance(secret_dir, str) else None
            if not isinstance(secret, Mapping) or set(secret) != {"file"} or not isinstance(secret.get("file"), str) or expected is None or os.path.normpath(secret["file"]) != expected:
                errors.append(ValidationError(path, "secret must use the site secret file"))

    _policy_walk(model, "$", errors)
    if errors:
        raise ValidationErrors(errors)
