"""The module, plugin and site contracts: file layout and metadata.

Rules about what a service may contain (image pins, health, binds, caps, ...) are not here. They run in
compose_policy on the rendered model, after `docker compose config` has merged overrides and normalized
every short form, so each rule there is a plain lookup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from .contracts import ValidationError, ValidationErrors

TIERS = ("app", "edge")
# Keys a site.env may define. The first four are the only ones a site may declare as bind roots.
ROOT_KEYS = ("APP_DATA_DIR", "MEDIA_HDD_PATH", "MEDIA_NVME_PATH", "MEDIA_PLEX_PATH")
# Media roles: where this site keeps movies, TV and downloads. Each must sit under a declared bind root.
MEDIA_ROLE_KEYS = ("MOVIES_DIR", "TV_DIR", "DOWNLOADS_DIR")
SITE_ENV_KEYS = frozenset(
    {*ROOT_KEYS, *MEDIA_ROLE_KEYS, "PUID", "PGID", "TZ", "HOMELAB_SECRET_DIR", "BASE_DOMAIN", "TAILSCALE_IP"}
)
_NAME = re.compile(r"[a-z][a-z0-9-]{0,62}")
_REFERENCE = re.compile(r"\$\{(\w+)\}")
_MODULE_SUFFIX = ".compose.yml"


@dataclass(frozen=True)
class Plugin:
    name: str
    root: Path
    image_namespace: str | None


@dataclass(frozen=True)
class Site:
    root: Path
    config: dict[str, Any]
    env: dict[str, str]
    plugins: dict[str, Plugin] = field(default_factory=dict)


def load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationError(str(path), f"cannot read YAML: {exc}") from exc


def read_env(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; blank lines and # comments are ignored."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


def _fields(
    value: Any, where: str, required: Mapping[str, type], optional: Mapping[str, type] | None = None
) -> list[ValidationError]:
    """A closed object: exactly these keys, each of the given type."""
    if not isinstance(value, dict):
        return [ValidationError(where, "expected a mapping")]
    kinds = {**required, **(optional or {})}
    errors = [ValidationError(f"{where}.{key}", "missing") for key in required if key not in value]
    errors += [ValidationError(f"{where}.{key}", "unknown field") for key in value if key not in kinds]
    return errors + [
        ValidationError(f"{where}.{key}", f"expected {kinds[key].__name__}")
        for key in value
        if key in kinds and not isinstance(value[key], kinds[key])
    ]


def check_variables(text: str, env: Mapping[str, str], where: str) -> list[ValidationError]:
    """Modules may interpolate only ${NAME}, with NAME defined in site.env ($$ is a literal $)."""
    errors = [
        ValidationError(where, f"undeclared site.env key {name}")
        for name in _REFERENCE.findall(text)
        if name not in env
    ]
    if "$" in _REFERENCE.sub("", text.replace("$$", "")):
        errors.append(ValidationError(where, "variables must use ${NAME} syntax"))
    return errors


def _module_name(path: Path) -> str:
    return path.name.removesuffix(_MODULE_SUFFIX)


def find_module(root: Path, service: str) -> Path | None:
    matches = [path for tier in TIERS if (path := root / "modules" / tier / f"{service}{_MODULE_SUFFIX}").exists()]
    if len(matches) > 1:
        raise ValidationError(f"$.services.{service}", "module exists in both tiers")
    return matches[0] if matches else None


def check_module(path: Path, env: Mapping[str, str]) -> str:
    """Validate one modules/<tier>/<service>.compose.yml file; return its tier."""
    errors = []
    if not path.name.endswith(_MODULE_SUFFIX) or path.parent.name not in TIERS or path.parent.parent.name != "modules":
        errors.append(ValidationError(str(path), "module must be modules/<app|edge>/<service>.compose.yml"))
    service = _module_name(path)
    services = (load_yaml(path) or {}).get("services")
    if not isinstance(services, dict) or list(services) != [service]:
        errors.append(ValidationError(f"{path}:services", f"must define exactly one service, named {service}"))
    errors += check_variables(path.read_text(encoding="utf-8"), env, str(path))
    if errors:
        raise ValidationErrors(errors)
    return path.parent.name


def load_plugin(name: str, root: Path, env: Mapping[str, str], platform: Path) -> Plugin:
    """Validate a plugin folder: metadata, README, and its modules (none may reuse a platform module name)."""
    metadata = load_yaml(root / "homelab-plugin.yaml")
    errors = _fields(
        metadata,
        f"{name}:homelab-plugin.yaml",
        {"contract": int, "name": str, "description": str},
        {"image_namespace": str},
    )
    if not errors and (
        metadata["contract"] != 1 or not _NAME.fullmatch(metadata["name"]) or metadata.get("image_namespace") == ""
    ):
        errors.append(
            ValidationError(
                f"{name}:homelab-plugin.yaml", "contract must be 1, name a lowercase id, image_namespace non-empty"
            )
        )
    modules = sorted(root.glob("modules/*/*.compose.yml"))
    readme = (root / "README.md").read_text(encoding="utf-8") if (root / "README.md").exists() else ""
    if not modules:
        errors.append(ValidationError(f"{name}:modules", "plugin must ship a module"))
    for module in modules:
        service = _module_name(module)
        if find_module(platform, service) is not None:
            errors.append(ValidationError(f"{name}:modules", f"{service} reuses a platform module name"))
        if not re.search(rf"(?<![\w-]){re.escape(service)}(?![\w-])", readme):
            errors.append(ValidationError(f"{name}:README.md", f"does not mention service {service}"))
        try:
            check_module(module, env)
        except ValidationErrors as error:
            errors += error.errors
    if errors:
        raise ValidationErrors(errors)
    return Plugin(name, root, metadata.get("image_namespace"))


def _check_site_config(config: Any, env: Mapping[str, str]) -> list[ValidationError]:
    errors = _fields(
        config,
        "site.yaml",
        {"contract": int, "site_id": str, "bind_roots": list, "plugins": dict, "services": dict, "managed": list},
        {"marker_roots": list},
    )
    if errors:
        return errors
    if config["contract"] != 1 or not _NAME.fullmatch(config["site_id"]):
        errors.append(ValidationError("site.yaml", "contract must be 1 and site_id a lowercase id"))
    if not set(config["bind_roots"]) <= set(ROOT_KEYS):
        errors.append(ValidationError("site.yaml.bind_roots", f"each must be one of {list(ROOT_KEYS)}"))
    if not set(config.get("marker_roots", [])) <= set(config["bind_roots"]):
        errors.append(ValidationError("site.yaml.marker_roots", "marker roots must be bind roots"))
    for name, source in config["plugins"].items():
        kind, value = next(iter(source.items())) if isinstance(source, dict) and len(source) == 1 else (None, None)
        if kind not in ("path", "repository") or not isinstance(value, str):
            errors.append(
                ValidationError(f"site.yaml.plugins.{name}", "must be {path: DIR} or {repository: OWNER/REPO}")
            )
    for name, spec in config["services"].items():
        errors += _fields(spec or {}, f"site.yaml.services.{name}", {}, {"from": str, "state": str})
        if isinstance(spec, dict) and spec.get("state", "running") not in ("running", "stopped"):
            errors.append(ValidationError(f"site.yaml.services.{name}.state", "must be running or stopped"))
    errors += [ValidationError(f"site.env.{key}", "unknown key") for key in env if key not in SITE_ENV_KEYS]
    return errors


def load_site(root: Path, platform: Path, plugin_dirs: Mapping[str, Path] | None = None) -> Site:
    """Validate site.yaml, site.env and every plugin the site names.

    A {path: DIR} plugin is found relative to the site; a {repository: ...} plugin must be passed in
    plugin_dirs (the release fetches it at its verified SHA). A plugin that can't be found is an error.
    """
    root = Path(root).resolve()
    config, env = load_yaml(root / "site.yaml"), read_env(root / "site.env")
    errors = _check_site_config(config, env)
    if errors:
        raise ValidationErrors(errors)
    plugins = {}
    for name, source in config["plugins"].items():
        folder = (plugin_dirs or {}).get(name) or (root / source["path"] if "path" in source else None)
        if folder is None:
            raise ValidationError(f"site.yaml.plugins.{name}", "repository plugin was not supplied")
        plugins[name] = load_plugin(name, Path(folder).resolve(), env, Path(platform))
    return Site(root, config, env, plugins)
