"""Compile a rendered Compose model and site overlay into a release bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping

import yaml

from .bundle import build, bundle_sha256, verify
from .compose_policy import validate_compose_model
from .contracts import ValidationError, load_application_manifest


_APPROVED_DESTINATIONS = Path(__file__).parents[2] / "config/release/approved-ghcr-destinations.json"
_DIGEST_REF = re.compile(r"^ghcr\.io/jon-robbins/homelab/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")


def _approved_repositories() -> dict[str, str]:
    try:
        value = json.loads(_APPROVED_DESTINATIONS.read_bytes())
        destinations = value["destinations"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValidationError("$.destinations", "invalid approved destination config") from error
    if not isinstance(destinations, list):
        raise ValidationError("$.destinations", "expected destination array")
    prefix = "ghcr.io/jon-robbins/homelab/"
    return {destination.removeprefix(prefix): destination for destination in destinations}


def _pin_images(document: dict[str, Any], model: dict[str, Any], image_refs: Mapping[str, str]) -> None:
    approved = _approved_repositories()
    applications = document.get("applications")
    if not isinstance(applications, list):
        raise ValidationError("$.applications", "expected application array")
    if not isinstance(image_refs, Mapping) or not all(isinstance(key, str) and isinstance(value, str) for key, value in image_refs.items()):
        raise ValidationError("$.image_refs", "expected image name to digest reference map")
    application_names = {application["name"] for application in applications}
    extra = sorted(set(image_refs) - application_names)
    if extra:
        raise ValidationError("$.image_refs", f"no matching application: {extra[0]}")
    missing = sorted(application_names - set(image_refs))
    if missing:
        raise ValidationError("$.image_refs", f"missing image reference for application: {missing[0]}")
    services = _managed_services(model)
    all_services = model["services"]
    for service_name, service in all_services.items():
        if not isinstance(service, dict) or "image" not in service:
            continue
        if service.get("image") not in approved.values():
            raise ValidationError(
                f"$.services.{service_name}.image",
                "must be a bare approved GHCR repository",
            )
        if service_name not in services:
            raise ValidationError(
                f"$.services.{service_name}.image",
                "image is not permitted for an unmanaged service",
            )
    for application in applications:
        name = application["name"]
        service = services[name]
        for path, owner in ((f"$.applications.{name}.image", application), (f"$.services.{name}.image", service)):
            if not isinstance(owner, dict) or owner.get("image") not in approved.values():
                raise ValidationError(path, "must be a bare approved GHCR repository")
        reference = image_refs[name]
        if not _DIGEST_REF.fullmatch(reference):
            raise ValidationError(f"$.image_refs.{name}", "must be a digest-pinned GHCR reference")
        if not reference.startswith(application["image"] + "@"):
            raise ValidationError(f"$.image_refs.{name}", "does not match the application's approved repository")
        application["image"] = reference
        service["image"] = reference


def _load_document(path: Path) -> Any:
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            return yaml.safe_load(path.read_bytes())
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValidationError("$", f"invalid source {path.name}: {exc}") from exc


def _installed_ids(path: Path) -> set[str]:
    value = _load_document(path)
    if isinstance(value, Mapping):
        value = value.get("probes", value.get("installed_probes"))
    if value is None:
        value = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationError("$.probes", "expected probe ID array")
    return set(value)


def _managed_services(model: Mapping[str, Any]) -> Mapping[str, Any]:
    services = model.get("services")
    if not isinstance(services, dict):
        raise ValidationError("$.services", "expected object")
    declared = model.get("x-homelab-managed-services")
    if declared is not None:
        if not isinstance(declared, list) or not all(isinstance(item, str) for item in declared):
            raise ValidationError("$.x-homelab-managed-services", "expected service name array")
        missing = [name for name in declared if name not in services]
        if missing:
            raise ValidationError("$.x-homelab-managed-services", f"unknown service: {missing[0]}")
        return {name: services[name] for name in declared}
    marked = {
        name: service
        for name, service in services.items()
        if isinstance(service, dict)
        and (
            service.get("x-homelab-managed") is True
            or (
                isinstance(service.get("labels"), dict)
                and service["labels"].get("com.homelab.managed") in {True, "true", "1"}
            )
        )
    }
    if marked:
        return marked
    return services


def _check_application_alignment(manifest: Mapping[str, Any], model: Mapping[str, Any]) -> None:
    applications = {application["name"]: application for application in manifest["applications"]}
    services = _managed_services(model)
    service_names = set(services)
    if set(applications) != service_names:
        missing = sorted(set(applications) - service_names)
        extra = sorted(service_names - set(applications))
        detail = []
        if missing:
            detail.append(f"missing from Compose: {missing[0]}")
        if extra:
            detail.append(f"extra managed service: {extra[0]}")
        raise ValidationError("$.applications", "; ".join(detail))
    for name, application in applications.items():
        service = services[name]
        if not isinstance(service, dict):
            raise ValidationError(f"$.services.{name}", "expected object")
        image = service.get("image")
        if image != application["image"]:
            raise ValidationError(
                f"$.services.{name}.image",
                f"does not match manifest image for {name}",
            )


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def compile_bundle(
    compose: Path,
    manifest: Path,
    installed_probes: Path,
    site_policy: Path,
    files: Path,
    output: Path,
    image_refs: Mapping[str, str],
) -> str:
    """Run the release gates and write a deterministic archive at *output*."""
    model = _load_document(compose)
    if not isinstance(model, dict):
        raise ValidationError("$", "expected rendered Compose object")
    policy = _load_document(site_policy)
    if not isinstance(policy, Mapping):
        raise ValidationError("$.site_policy", "expected object")
    probes = _installed_ids(installed_probes)
    gpu_profiles = policy.get("gpu_profiles", policy.get("installed_gpu_profiles", ()))
    if not isinstance(gpu_profiles, (list, tuple, set, frozenset)):
        raise ValidationError("$.site_policy.gpu_profiles", "expected profile name array")
    document = _load_document(manifest)
    if not isinstance(document, dict):
        raise ValidationError("$", "expected application manifest object")
    _check_application_alignment(document, model)
    _pin_images(document, model, image_refs)
    validate_compose_model(model, root=compose.parent)
    document = load_application_manifest(
        json.dumps(document).encode(),
        installed_probes=probes,
        gpu_profiles=set(gpu_profiles),
        site_policy=policy,
    )
    _check_application_alignment(document, model)
    if not files.is_dir() or files.is_symlink():
        raise ValidationError("$.files", "expected regular directory")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-compile-") as temporary:
        root = Path(temporary)
        _write_json(root / "applications.json", document)
        (root / "compose.yaml").write_text(
            yaml.safe_dump(model, sort_keys=True, default_flow_style=False), encoding="utf-8"
        )
        _write_json(root / "probe-targets.json", {"probes": sorted(probes)})
        shutil.copytree(files, root / "files", symlinks=True)
        data = build(root)
    verify(data)
    output.write_bytes(data)
    return bundle_sha256(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", "--compose-json", dest="compose", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--installed-probes", "--probes", dest="installed_probes", type=Path)
    parser.add_argument("--site-policy", "--policy", dest="site_policy", type=Path)
    parser.add_argument("--files", type=Path)
    parser.add_argument("--output", "--bundle", dest="output", type=Path)
    parser.add_argument("--image-refs", type=Path)
    parser.add_argument("paths", nargs="*", type=Path, help="compose manifest probes policy files output image_refs")
    args = parser.parse_args(argv)
    options = [args.compose, args.manifest, args.installed_probes, args.site_policy, args.files, args.output, args.image_refs]
    if any(option is None for option in options):
        if any(option is not None for option in options) or len(args.paths) != 7:
            parser.error("provide all seven named options or seven positional inputs")
        options = args.paths
    compose, manifest, installed_probes, site_policy, files, output, image_refs_path = options
    try:
        image_refs = _load_document(image_refs_path)
        digest = compile_bundle(
            compose, manifest, installed_probes, site_policy, files, output, image_refs
        )
    except (OSError, ValueError, ValidationError) as exc:
        print(f"release compile failed: {exc}")
        return 1
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
