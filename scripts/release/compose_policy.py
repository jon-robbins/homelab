"""Validation of the safe operational subset of a rendered Compose model."""

from __future__ import annotations

import argparse
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


def _path(path: str, key: str) -> str:
    return f"{path}.{key}" if path != "$" else f"$.{key}"


def _array_path(path: str, index: int) -> str:
    return f"{path}[{index}]"


def _expand(value: str, variables: Mapping[str, str], path: str) -> str:
    """Expand the useful Compose ${VAR} forms without consulting ambient env."""

    def replace(match: re.Match[str]) -> str:
        expression = match.group(1)
        name_match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", expression)
        if name_match is None:
            return match.group(0)
        name = name_match.group(0)
        operator = expression[len(name) :]
        present = name in variables
        value = variables.get(name, "")
        if operator in ("",):
            return value if present else match.group(0)
        if operator.startswith(":-") or operator.startswith("-"):
            use_default = not present or (operator.startswith(":-") and value == "")
            return operator[2 if operator.startswith(":-") else 1 :] if use_default else value
        if operator.startswith(":+") or operator.startswith("+"):
            use_alternate = present and (not operator.startswith(":+") or value != "")
            return operator[2 if operator.startswith(":+") else 1 :] if use_alternate else ""
        # ${VAR?message} and ${VAR:?message} are intentionally left unresolved;
        # the rendered model must not contain Compose interpolation syntax.
        return match.group(0)

    expanded = _VARIABLE.sub(replace, value)
    if _VARIABLE.search(expanded):
        raise ValidationError(path, "unresolved variable expression")
    return expanded


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _forbidden_host_path(source: str, root: Path) -> str | None:
    candidate = Path(source)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    forbidden = [Path("/"), Path("/srv/homelab"), Path("/srv/vpn"), Path("/srv"), *(_HOME_PREFIXES)]
    # Path('/') is deliberately first: every absolute path is below it, so it
    # is only a forbidden exact path, whereas the other entries include their
    # descendants.
    if resolved == Path("/"):
        return "/"
    for parent in forbidden[1:]:
        if resolved == parent or _is_within(resolved, parent):
            return str(parent)
    home = Path.home().resolve(strict=False)
    if resolved == home or _is_within(resolved, home):
        return str(home)
    return None


def _check_bind(value: Any, path: str, *, root: Path, variables: Mapping[str, str], errors: list[ValidationError]) -> None:
    if isinstance(value, str):
        errors.append(ValidationError(path, "short-syntax binds are not permitted"))
        return
    if not isinstance(value, dict) or value.get("type") != "bind":
        return
    bind = value.get("bind")
    if not isinstance(bind, dict) or bind.get("create_host_path") is not False:
        errors.append(ValidationError(_path(path, "bind.create_host_path"), "binds must set create_host_path to false"))
    source = value.get("source", value.get("src"))
    if not isinstance(source, str):
        return
    try:
        expanded = _expand(source, variables, _path(path, "source"))
    except ValidationError as error:
        errors.append(error)
        return
    forbidden = _forbidden_host_path(expanded, root)
    if forbidden is not None:
        errors.append(ValidationError(_path(path, "source"), f"bind host path resolves into forbidden path {forbidden}"))


def _walk(value: Any, path: str, *, root: Path, variables: Mapping[str, str], errors: list[ValidationError]) -> None:
    if isinstance(value, str):
        try:
            expanded = _expand(value, variables, path)
        except ValidationError as error:
            errors.append(error)
            expanded = value
        if "docker.sock" in expanded:
            errors.append(ValidationError(path, "docker.sock is not permitted"))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, _array_path(path, index), root=root, variables=variables, errors=errors)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        item_path = _path(path, str(key))
        try:
            expanded_item = _expand(item, variables, item_path) if isinstance(item, str) else item
        except ValidationError:
            expanded_item = item
        if key in _FORBIDDEN_KEYS:
            errors.append(ValidationError(item_path, _FORBIDDEN_KEYS[key]))
        if key in {"network_mode", "pid", "ipc"} and expanded_item == "host":
            errors.append(ValidationError(item_path, f"{key}: host is not permitted"))
        if key == "privileged" and item is True:
            errors.append(ValidationError(item_path, "privileged is not permitted"))
        if key == "include":
            errors.append(ValidationError(item_path, "remote includes are not permitted"))
        if key == "volumes" and isinstance(item, list):
            for index, volume in enumerate(item):
                _check_bind(volume, _array_path(item_path, index), root=root, variables=variables, errors=errors)
        _walk(item, item_path, root=root, variables=variables, errors=errors)


def validate_compose_model(
    model: Any,
    *,
    root: str | os.PathLike[str] = ".",
    variables: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate and return a rendered Docker Compose model.

    ``variables`` is explicit by design: validation never imports values from
    the process environment, so an ambient ``.env`` cannot silently affect it.
    """

    if not isinstance(model, dict):
        raise ValidationError("$", "expected rendered Compose object")
    errors: list[ValidationError] = []
    _walk(model, "$", root=Path(root).resolve(strict=False), variables=variables or {}, errors=errors)
    if errors:
        raise ValidationErrors(errors)
    return model


validate = validate_compose_model


def load_compose_model(source: str | bytes, **kwargs: Any) -> dict[str, Any]:
    try:
        model = json.loads(source)
    except json.JSONDecodeError as exc:
        raise ValidationError("$", f"invalid JSON: {exc.msg}") from exc
    return validate_compose_model(model, **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path, help="rendered Compose JSON file")
    parser.add_argument("--root", type=Path, default=Path("."), help="base directory for relative bind sources")
    args = parser.parse_args(argv)
    try:
        load_compose_model(args.model.read_bytes(), root=args.root)
    except (OSError, ValidationError) as exc:
        print(f"invalid Compose policy: {exc}")
        return 1
    print("Compose operational-subset policy: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
