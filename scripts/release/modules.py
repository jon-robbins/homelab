"""Validate platform modules, sites, and code-free plugins."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from .contracts import ValidationError, ValidationErrors


_DIGEST = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z" "0-9_]*)\}")
_PROBE = re.compile(r"^http://:[0-9]+.*$")
_SCHEMAS = Path(__file__).parents[2] / "schemas"


def _load(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationError("$", f"cannot read YAML: {exc}") from exc


def _error(path: str, message: str) -> ValidationError:
    return ValidationError(path, message)


def _schema_check(value: Any, schema: Mapping[str, Any], path: str = "$", root: Mapping[str, Any] | None = None) -> None:
    root = root or schema
    if "$ref" in schema:
        target: Any = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        _schema_check(value, target, path, root)
        return
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            try:
                _schema_check(value, option, path, root)
            except ValidationError:
                continue
            matches += 1
        if matches != 1:
            raise _error(path, "must match exactly one schema alternative")
    expected = schema.get("type")
    type_ok = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
    }
    if expected in type_ok and not type_ok[expected]:
        raise _error(path, f"expected {expected}")
    if "const" in schema and value != schema["const"]:
        raise _error(path, f"must equal {schema['const']!r}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise _error(f"{path}.{key}", "missing required field")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    raise _error(f"{path}.{key}", "unknown field")
        elif isinstance(schema.get("additionalProperties"), Mapping):
            child = schema["additionalProperties"]
            for key in value:
                if key not in properties:
                    _schema_check(value[key], child, f"{path}.{key}", root)
        for key, child in properties.items():
            if key in value:
                _schema_check(value[key], child, f"{path}.{key}", root)
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _schema_check(item, schema["items"], f"{path}[{index}]")
    if "pattern" in schema and (not isinstance(value, str) or re.search(schema["pattern"], value) is None):
        raise _error(path, "does not match required pattern")


def _validate_schema(value: Any, name: str) -> None:
    try:
        schema = json.loads((_SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("$", f"cannot read {name} schema: {exc}") from exc
    _schema_check(value, schema)


def _roots(site_env: Mapping[str, str]) -> tuple[Path, ...]:
    values = [value for key, value in site_env.items() if key == "APP_DATA_DIR" or "MEDIA" in key]
    return tuple(Path(value).expanduser().resolve(strict=False) for value in values if value)


def _expand(value: str, site_env: Mapping[str, str], path: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in site_env:
            raise _error(path, f"unknown site.env key {name}")
        return site_env[name]

    return _VARIABLE.sub(replace, value)


def validate_module(path: str | Path, site_env: Mapping[str, str]) -> dict[str, Any]:
    """Validate one ``modules/<tier>/<service>.yml`` and return its Compose document."""
    module_path = Path(path)
    errors: list[ValidationError] = []
    if (module_path.suffix != ".yml" or module_path.parent.name not in {"app", "edge"}
            or module_path.parent.parent.name != "modules"):
        errors.append(_error("$", "module must be modules/<app|edge>/<service>.yml"))
    document = _load(module_path)
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict) or len(services) != 1:
        errors.append(_error("$.services", "module must contain exactly one service"))
        if errors:
            raise ValidationErrors(errors)
    service_name, service = next(iter(services.items()))
    if module_path.stem != service_name:
        errors.append(_error("$.services", "service name must match the file name"))
    if not isinstance(service, dict):
        errors.append(_error(f"$.services.{service_name}", "expected service object"))
        raise ValidationErrors(errors)
    image = service.get("image")
    if not isinstance(image, str) or _DIGEST.fullmatch(image) is None:
        errors.append(_error(f"$.services.{service_name}.image", "image must be pinned by digest"))
    labels = service.get("labels", {})
    if isinstance(labels, list):
        labels = dict(item.split("=", 1) for item in labels if "=" in item)
    if not isinstance(labels, dict):
        labels = {}
    state = labels.get("homelab.state")
    if state not in {"stateful", "stateless"}:
        errors.append(_error(f"$.services.{service_name}.labels", "homelab.state must be stateful or stateless"))
    healthcheck = service.get("healthcheck")
    effective_healthcheck = isinstance(healthcheck, dict) and not healthcheck.get("disable")
    if isinstance(healthcheck, dict) and healthcheck.get("test") == ["NONE"]:
        effective_healthcheck = False
    if not effective_healthcheck:
        probe = labels.get("homelab.probe")
        if not isinstance(probe, str) or _PROBE.fullmatch(probe) is None:
            errors.append(_error(f"$.services.{service_name}", "service needs a healthcheck or homelab.probe"))
    roots = _roots(site_env)
    volumes = service.get("volumes", [])
    for index, volume in enumerate(volumes if isinstance(volumes, list) else []):
        if isinstance(volume, str):
            parts = volume.split(":")
            source = parts[0] if len(parts) >= 2 else None
        elif isinstance(volume, dict) and volume.get("type") == "bind":
            source = volume.get("source", volume.get("src"))
        else:
            continue
        if not isinstance(source, str):
            continue
        try:
            resolved = Path(_expand(source, site_env, f"$.services.{service_name}.volumes[{index}]")).resolve(strict=False)
        except ValidationError as error:
            errors.append(error)
            continue
        if not any(resolved == root or root in resolved.parents for root in roots):
            errors.append(_error(f"$.services.{service_name}.volumes[{index}]", "bind is outside site.env data/media roots"))
    if errors:
        raise ValidationErrors(errors)
    return document


def _module_names(root: Path) -> set[str]:
    return {path.stem for path in root.glob("modules/*/*.yml")}


def validate_plugin(path: str | Path, site_env: Mapping[str, str], platform_modules: set[str] | None = None) -> dict[str, Any]:
    plugin = Path(path)
    metadata = _load(plugin / "homelab-plugin.yaml")
    _validate_schema(metadata, "plugin")
    modules = sorted(plugin.glob("modules/*/*.yml"))
    if not modules:
        raise ValidationError("$.modules", "plugin must ship a module")
    names = platform_modules or set()
    errors: list[ValidationError] = []
    readme = plugin / "README.md"
    text = readme.read_text(encoding="utf-8") if readme.exists() else ""
    for module in modules:
        validate_module(module, site_env)
        if module.stem in names:
            errors.append(_error("$.modules", f"plugin service collides with platform module: {module.stem}"))
        if re.search(rf"(?<![\w-]){re.escape(module.stem)}(?![\w-])", text) is None:
            errors.append(_error("$.README.md", f"README does not mention service {module.stem}"))
    if errors:
        raise ValidationErrors(errors)
    return metadata


def validate_site(path: str | Path) -> dict[str, Any]:
    site = Path(path)
    document = _load(site / "site.yaml")
    _validate_schema(document, "site")
    env: dict[str, str] = {}
    env_path = site / "site.env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key] = value
    platform = _module_names(site.parent) if (site.parent / "modules").exists() else set()
    for name, source in (document.get("plugins") or {}).items():
        if isinstance(source, dict) and "path" in source:
            validate_plugin(site / source["path"], env, platform)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.path.name == "site.yaml":
            validate_site(args.path.parent)
        elif (args.path / "homelab-plugin.yaml").exists():
            validate_plugin(args.path, {})
        else:
            validate_module(args.path, {})
    except (OSError, ValidationError) as error:
        print(f"invalid module contract: {error}")
        return 1
    print("module contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
