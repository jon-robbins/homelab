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
_VARIABLE = re.compile(r"\$\{([A-Za-z_]\w*)\}", re.ASCII)
_PROBE = re.compile(r"^http://:[0-9]+/.*$")
_SCHEMAS = Path(__file__).parents[2] / "schemas"
_IMAGE_POLICY = Path(__file__).parents[2] / "config/release/image-policy.json"
_FIRST_PARTY = "ghcr.io/jon-robbins/homelab/"


def _load(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationError("$", f"cannot read YAML: {exc}") from exc


def _error(path: str, message: str) -> ValidationError:
    return ValidationError(path, message)


def _schema_check(value: Any, schema: Mapping[str, Any], path: str = "$", root: Mapping[str, Any] | None = None) -> None:
    root = root or schema
    expected = schema.get("type")
    type_ok = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
    }
    if expected in type_ok and not type_ok[expected]:
        raise _error(path, f"expected {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise _error(path, f"must be one of {schema['enum']!r}")
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


def _site_env_keys() -> set[str]:
    schema = json.loads((_SCHEMAS / "site.schema.json").read_text(encoding="utf-8"))
    return set(schema["properties"]["site_env"]["properties"])


def _roots(site_env: Mapping[str, str], bind_root_keys: list[str] | None = None) -> tuple[Path, ...]:
    keys = bind_root_keys if bind_root_keys is not None else [
        key for key in ("APP_DATA_DIR", "MEDIA_HDD_PATH", "MEDIA_NVME_PATH") if key in site_env
    ]
    values = [site_env[key] for key in keys if key in site_env]
    return tuple(Path(value).expanduser().resolve(strict=False) for value in values if value)


def _expand(value: str, site_env: Mapping[str, str], path: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in site_env:
            raise _error(path, f"unknown site.env key {name}")
        return site_env[name]

    expanded = _VARIABLE.sub(replace, value)
    if "$" in expanded:
        raise _error(path, "variables must use ${NAME} syntax")
    return expanded


def validate_module(path: str | Path, site_env: Mapping[str, str], bind_root_keys: list[str] | None = None,
                    plugin_namespace: str | None = None) -> dict[str, Any]:
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
    allowed_keys = set(site_env)

    def check_variables(value: Any, field: str) -> None:
        if isinstance(value, str):
            for match in _VARIABLE.finditer(value):
                if match.group(1) not in allowed_keys:
                    errors.append(_error(field, f"undeclared site.env key {match.group(1)}"))
            if "$" in _VARIABLE.sub("", value):
                errors.append(_error(field, "variables must use ${NAME} syntax"))
        elif isinstance(value, dict):
            for key, child in value.items():
                check_variables(child, f"{field}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                check_variables(child, f"{field}[{index}]")

    check_variables(document, "$")
    image = service.get("image")
    if not isinstance(image, str) or _DIGEST.fullmatch(image) is None:
        errors.append(_error(f"$.services.{service_name}.image", "image must be pinned by digest"))
    elif not image.split("@", 1)[0].startswith(_FIRST_PARTY):
        namespaces = json.loads(_IMAGE_POLICY.read_text(encoding="utf-8"))["namespaces"]
        base = image.split("@", 1)[0]
        repository = base + "/"
        plugin_prefix = f"{plugin_namespace.rstrip('/')}/" if plugin_namespace else None
        if not any(repository.startswith(namespace) for namespace in namespaces) and not (
            plugin_prefix and repository.startswith(plugin_prefix)
        ):
            errors.append(_error(f"$.services.{service_name}.image", "image namespace is not permitted"))
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
    roots = _roots(site_env, bind_root_keys)
    volumes = service.get("volumes", [])
    for index, volume in enumerate(volumes if isinstance(volumes, list) else []):
        if isinstance(volume, str):
            parts = volume.split(":")
            source = parts[0] if len(parts) >= 2 else None
            is_bind = isinstance(source, str) and source.startswith(("/", ".", "~", "${"))
        elif isinstance(volume, dict) and volume.get("type") == "bind":
            source = volume.get("source", volume.get("src"))
            is_bind = True
        else:
            continue
        if not is_bind or not isinstance(source, str):
            continue
        if source.startswith("~"):
            errors.append(_error(f"$.services.{service_name}.volumes[{index}]", "use ${NAME} roots"))
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


def validate_plugin(path: str | Path, site_env: Mapping[str, str], platform_modules: set[str] | None = None,
                    bind_root_keys: list[str] | None = None) -> dict[str, Any]:
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
        namespace = metadata.get("image_namespace")
        compose = validate_module(module, site_env, bind_root_keys,
                                  namespace if isinstance(namespace, str) else None)
        if "image_namespace" in metadata:
            image = next(iter(compose["services"].values()))["image"].split("@", 1)[0]
            if not image.startswith(f"{namespace}/"):
                errors.append(_error("$.image_namespace", f"image for {module.stem} is outside {namespace}"))
        if module.stem in names:
            errors.append(_error("$.modules", f"plugin service collides with platform module: {module.stem}"))
        if re.search(rf"(?<![\w-]){re.escape(module.stem)}(?![\w-])", text) is None:
            errors.append(_error("$.README.md", f"README does not mention service {module.stem}"))
    if errors:
        raise ValidationErrors(errors)
    return metadata


def validate_site(path: str | Path, platform_dir: str | Path,
                  plugin_dirs: list[str | Path] | Mapping[str, str | Path] | None = None) -> dict[str, Any]:
    site = Path(path)
    document = _load(site / "site.yaml")
    _validate_schema(document, "site")
    marker_roots = document.get("marker_roots", [])
    if not set(marker_roots).issubset(document.get("bind_roots", [])):
        raise ValidationError("$.marker_roots", "marker roots must be bind roots")
    env: dict[str, str] = {}
    env_path = site / "site.env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key] = value
    platform = _module_names(Path(platform_dir))
    root_keys = document["bind_roots"]
    if isinstance(plugin_dirs, Mapping):
        paths = {name: Path(value) for name, value in plugin_dirs.items()}
    elif plugin_dirs:
        paths = {Path(value).name: Path(value) for value in plugin_dirs}
    else:
        paths = {}
    for name, source in (document.get("plugins") or {}).items():
        if not isinstance(source, dict):
            continue
        if "path" in source:
            plugin_path = paths.get(name, site / source["path"])
        elif "repository" in source:
            plugin_path = paths.get(name)
        else:
            continue
        if plugin_path is not None:
            validate_plugin(plugin_path, env, platform, root_keys)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.path.name == "site.yaml":
            validate_site(args.path.parent, args.path.parent.parent)
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
