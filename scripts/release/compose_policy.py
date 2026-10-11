"""Validation of the safe operational subset of a rendered Compose model."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

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
