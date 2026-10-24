"""Policy v2 (D14) and the module rules, checked on the rendered Compose model.

The model is `docker compose config --format json` output: overrides merged, variables resolved and every
short form (volumes, labels, healthchecks, configs) normalized. So each rule below is a small function of one
service that yields what's wrong with it; check_policy runs them all and reports every violation at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterator, Mapping

from .contracts import ValidationError, ValidationErrors

FIRST_PARTY = "ghcr.io/jon-robbins/homelab/"
PROTECTED = ("/srv/homelab", "/srv/vpn", "/run/docker.sock", "/var/run/docker.sock")
_CONFIG = Path(__file__).parents[2] / "config/release"
_PINNED = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")
_PROBE = re.compile(r"http://:[0-9]+/.*")
_BANNED_KEYS = {"build", "env_file", "post_start", "pre_stop", "include"}


@dataclass(frozen=True)
class Context:
    tiers: Mapping[str, str]  # service -> "app" | "edge"
    namespaces: Mapping[str, str | None]  # service -> its code plugin's image namespace, if any
    env: Mapping[str, str]  # site.env
    roots: tuple[str, ...]  # values of the site's declared bind roots


@cache
def _allowlist(name: str) -> dict[str, Any]:
    return json.loads((_CONFIG / name).read_bytes())


def under(path: str, parent: str) -> bool:
    path, parent = os.path.normpath(path), os.path.normpath(parent)
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def _overlaps(a: str, b: str) -> bool:
    return under(a, b) or under(b, a)


# --- rules on one service ---------------------------------------------------------------------------------

Rule = Callable[[str, Mapping[str, Any], Context], Iterator[str]]


def _tier(name: str, service: Mapping[str, Any], ctx: Context) -> Iterator[str]:
    if ctx.tiers.get(name) not in ("app", "edge"):
        yield "missing or invalid tier"


def _image(name: str, service: Mapping[str, Any], ctx: Context) -> Iterator[str]:
    image = service.get("image", "")
    if not _PINNED.fullmatch(image):
        yield "image must be pinned by digest (repo[:tag]@sha256:...)"
        return
    repository = image.split("@")[0]
    namespace = ctx.namespaces.get(name)
    if namespace:
        allowed = repository.startswith(namespace.rstrip("/") + "/")
    else:
        allowed = repository.startswith((FIRST_PARTY, *_allowlist("image-policy.json")["namespaces"]))
    if not allowed:
        yield f"image namespace is not permitted: {repository}"


def _health(name: str, service: Mapping[str, Any], ctx: Context) -> Iterator[str]:
    check = service.get("healthcheck") or {}
    active = check.get("test") not in (None, [], ["NONE"]) and not check.get("disable")
    probe = (service.get("labels") or {}).get("homelab.probe", "")
    if not active and not _PROBE.fullmatch(probe):
        yield "needs a healthcheck or a homelab.probe label (http://:<port>/<path>)"


def _state(name: str, service: Mapping[str, Any], ctx: Context) -> Iterator[str]:
    if (service.get("labels") or {}).get("homelab.state") not in ("stateful", "stateless"):
        yield "label homelab.state must be stateful or stateless"


def _namespaces(name: str, service: Mapping[str, Any], ctx: Context) -> Iterator[str]:
    if service.get("network_mode") == "host" and ctx.tiers.get(name) != "edge":
        yield "host networking is only for the edge tier"
    yield from (f"{key}: host is not permitted" for key in ("pid", "ipc") if service.get(key) == "host")


def _binds(name: str, service: Mapping[str, Any], ctx: Context) -> Iterator[str]:
    for volume in service.get("volumes") or []:
        source = volume.get("source", "") if volume.get("type") == "bind" else None
        if source is not None and not any(
            under(os.path.realpath(source), os.path.realpath(root)) for root in ctx.roots
        ):
            yield f"bind {source} is outside the declared site roots"


def _data_dir(name: str, service: Mapping[str, Any], ctx: Context) -> Iterator[str]:
    # homelab-backup snapshots a stateful service as ${APP_DATA_DIR}/<service>, so its app data must live there.
    data = ctx.env.get("APP_DATA_DIR")
    if data is None or (service.get("labels") or {}).get("homelab.state") != "stateful":
        return
    own = os.path.join(data, name)
    for volume in service.get("volumes") or []:
        source = volume.get("source", "") if volume.get("type") == "bind" else ""
        if source and under(source, data) and not under(source, own):
            yield f"stateful bind {source} must sit under {own}, which homelab-backup snapshots"


def _caps(name: str, service: Mapping[str, Any], ctx: Context) -> Iterator[str]:
    allowed = set(_allowlist("cap-allowlist.json")["capabilities"])
    yield from (
        f"capability {cap} is not on the allowlist"
        for cap in service.get("cap_add") or []
        if cap.removeprefix("CAP_") not in allowed
    )


def _gpu_request(request: Mapping[str, Any]) -> bool:
    gpu = _allowlist("gpu-allowlist.json")
    return request.get("driver") in (None, "", *gpu["drivers"]) and set(request.get("capabilities") or []) <= set(
        gpu["capabilities"]
    )


def _gpu(name: str, service: Mapping[str, Any], ctx: Context) -> Iterator[str]:
    gpu = _allowlist("gpu-allowlist.json")
    if "runtime" in service and service["runtime"] not in gpu["runtimes"]:
        yield f"runtime {service['runtime']} is not on the GPU allowlist"
    for device in service.get("devices") or []:
        source = device.get("source", "") if isinstance(device, Mapping) else device.split(":", 1)[0]
        if not any(under(source, allowed) for allowed in gpu["devices"]):
            yield f"device {source} is not on the GPU allowlist"
    gpus = service.get("gpus")
    if gpus is not None and gpus != "all" and not (isinstance(gpus, list) and all(map(_gpu_request, gpus))):
        yield "gpus must be 'all' or allowlisted GPU requests"
    requests = (((service.get("deploy") or {}).get("resources") or {}).get("reservations") or {}).get("devices") or []
    if not all(map(_gpu_request, requests)):
        yield "deploy GPU request is not on the allowlist"


RULES: tuple[Rule, ...] = (_tier, _image, _health, _state, _namespaces, _binds, _data_dir, _caps, _gpu)


# --- rules on the site and the whole model ----------------------------------------------------------------


def _root_errors(ctx: Context) -> Iterator[ValidationError]:
    for root in ctx.roots:
        if not os.path.isabs(root):
            yield ValidationError("site.env", f"bind root {root} must be absolute")
            continue
        for protected in PROTECTED:
            exempt = protected == "/srv/homelab" and under(root, "/srv/homelab/data")
            if not exempt and (
                _overlaps(root, protected) or _overlaps(os.path.realpath(root), os.path.realpath(protected))
            ):
                yield ValidationError("site.env", f"bind root {root} overlaps protected path {protected}")


def _secret_errors(model: Mapping[str, Any], ctx: Context) -> Iterator[ValidationError]:
    directory = ctx.env.get("HOMELAB_SECRET_DIR")
    for name, secret in (model.get("secrets") or {}).items():
        # Compose adds `name` (<project>_<NAME>); anything else (environment, content) is a non-file source.
        if (
            directory is None
            or not set(secret) <= {"file", "name"}
            or secret.get("file") != os.path.join(directory, name)
        ):
            yield ValidationError(f"$.secrets.{name}", "secret must be {file: ${HOMELAB_SECRET_DIR}/NAME}")


def _banned(value: Any, path: str) -> Iterator[ValidationError]:
    """privileged, build, env_file, lifecycle hooks, include and the Docker socket are banned anywhere."""
    if isinstance(value, str) and "docker.sock" in value:
        yield ValidationError(path, "docker.sock is not permitted")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _banned(item, f"{path}[{index}]")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if key in _BANNED_KEYS or (key == "privileged" and item is True):
                yield ValidationError(f"{path}.{key}", f"{key} is not permitted")
            yield from _banned(item, f"{path}.{key}")


def check_policy(model: Mapping[str, Any], ctx: Context) -> None:
    """Raise ValidationErrors listing every violation in the rendered model."""
    errors = [*_root_errors(ctx), *_secret_errors(model, ctx), *_banned(model, "$")]
    for name, service in (model.get("services") or {}).items():
        errors += [
            ValidationError(f"$.services.{name}", message) for rule in RULES for message in rule(name, service, ctx)
        ]
    if errors:
        raise ValidationErrors(errors)
