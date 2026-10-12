"""Strict validators for the v1 release contracts.

JSON is the wire format.  YAML loading is provided only as a convenience for
human-authored source and uses PyYAML's safe loader.
"""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any, Mapping


class ValidationError(ValueError):
    """A contract violation with the JSON path at which it occurred."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


class ValidationErrors(ValidationError):
    """Several contract violations found during one validation pass."""

    def __init__(self, errors: list[ValidationError]) -> None:
        if not errors:
            raise ValueError("ValidationErrors requires at least one error")
        self.errors = tuple(errors)
        super().__init__(errors[0].path, "\n".join(str(error) for error in errors))


NAME_PATTERN = r"[a-z][a-z0-9-]{0,62}"
SECRET_NAME_PATTERN = r"[A-Z][A-Z0-9_]{0,127}"
GIT_SHA_PATTERN = r"[0-9a-f]{40}"
DIGEST_PATTERN = r"sha256:[0-9a-f]{64}"
# A registry/repository must contain a registry component and may include
# nested repository components. Tags are deliberately not accepted.
IMAGE_REF_PATTERN = rf"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]+)?/[a-z0-9][a-z0-9._/-]*@{DIGEST_PATTERN}"

# This is the field table consumed by the validator and mirrored by the
# published schema. The values are (required, type-name) metadata.
APPLICATION_FIELDS = {
    "name": (True, "Name"),
    "image": (True, "ImageRef"),
    "rollback": (True, "Rollback"),
    "depends_on": (True, "Name[]"),
    "secrets": (True, "SecretName[]"),
    "health_probe": (True, "ProbeID"),
    "semantic_probes": (True, "ProbeID[]"),
    "gpu": (False, "GPU"),
}
MANIFEST_FIELDS = {
    "schema": (True, "SchemaVersion"),
    "site_id": (False, "Name"),
    "applications": (True, "Application[]"),
}
SOURCES_FIELDS = {
    "homelab": (True, "GitSHA"),
    "homelab-site": (True, "GitSHA"),
    "tracker-integrations": (False, "GitSHA"),
}
RELEASE_RECORD_FIELDS = {
    "schema": (True, "SchemaVersion"),
    "site_id": (True, "Name"),
    "sequence": (True, "Sequence"),
    "tooling_git_sha": (True, "GitSHA"),
    "sources": (True, "Sources"),
    "images": (True, "ImageMap"),
    "bundle_sha256": (True, "Digest"),
    "rollback_floor": (True, "NonnegativeInteger"),
}
PROBE_RESULT_FIELDS = {
    "schema": (True, "SchemaVersion"),
    "probe": (True, "ProbeID"),
    "target": (True, "Name"),
    "release_sequence": (True, "Sequence|null"),
    "observed_image_id": (True, "Digest|null"),
    "result": (True, "ProbeResult"),
    "observed_at": (True, "Timestamp"),
    "duration_ms": (True, "NonnegativeInteger"),
    "detail": (True, "BoundedString"),
}
def _path(path: str, key: str) -> str:
    return f"{path}.{key}" if path != "$" else f"$.{key}"


def _array_path(path: str, index: int) -> str:
    return f"{path}[{index}]"


def _object(value: Any, path: str, fields: Mapping[str, tuple[bool, str]]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(path, "expected object")
    unknown = [key for key in value if key not in fields]
    if unknown:
        raise ValidationError(_path(path, str(unknown[0])), "unknown field")
    for key, (required, _) in fields.items():
        if required and key not in value:
            raise ValidationError(_path(path, key), "missing required field")
    return value


def _string(value: Any, path: str, pattern: str, typename: str) -> str:
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ValidationError(path, f"expected {typename}")
    return value


def validate_name(value: Any, path: str = "$") -> str:
    return _string(value, path, NAME_PATTERN, "Name")


def validate_secret_name(value: Any, path: str = "$") -> str:
    return _string(value, path, SECRET_NAME_PATTERN, "SecretName")


def validate_git_sha(value: Any, path: str = "$") -> str:
    return _string(value, path, GIT_SHA_PATTERN, "GitSHA")


def validate_digest(value: Any, path: str = "$") -> str:
    return _string(value, path, DIGEST_PATTERN, "Digest")


def validate_repository(value: Any, path: str = "$") -> str:
    return _string(value, path, rf"{NAME_PATTERN}/{NAME_PATTERN}", "Repository")


def validate_workflow_ref(value: Any, path: str = "$") -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(path, "expected WorkflowRef")
    return value


def validate_image_ref(value: Any, path: str = "$") -> str:
    return _string(value, path, IMAGE_REF_PATTERN, "ImageRef")


def validate_sequence(value: Any, path: str = "$") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63 - 1:
        raise ValidationError(path, "expected Sequence")
    return value


def validate_timestamp(value: Any, path: str = "$") -> str:
    if not isinstance(value, str):
        raise ValidationError(path, "expected Timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError(path, "expected UTC RFC3339 Timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValidationError(path, "expected UTC RFC3339 Timestamp")
    # RFC3339 requires a date/time separator and an explicit UTC offset.
    if "T" not in value or not (value.endswith("Z") or value[-6] in "+-"):
        raise ValidationError(path, "expected UTC RFC3339 Timestamp")
    return value


def validate_probe_id(value: Any, path: str = "$") -> str:
    return validate_name(value, path)


def validate_nonnegative_integer(value: Any, path: str = "$") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(path, "expected nonnegative integer")
    return value


def validate_positive_integer(value: Any, path: str = "$") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(path, "expected positive integer")
    return value


def _schema_one(value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != 1:
        raise ValidationError(path, "unsupported schema version; expected 1")


def _nullable(value: Any, path: str, validator) -> Any:
    return None if value is None else validator(value, path)


def validate_release_record(value: Any) -> dict[str, Any]:
    """Validate and return a ReleaseRecord mapping."""
    record = _object(value, "$", RELEASE_RECORD_FIELDS)
    _schema_one(record["schema"], "$.schema")
    validate_name(record["site_id"], "$.site_id")
    sequence = validate_sequence(record["sequence"], "$.sequence")
    validate_git_sha(record["tooling_git_sha"], "$.tooling_git_sha")

    sources_path = "$.sources"
    sources = _object(record["sources"], sources_path, SOURCES_FIELDS)
    for key in sources:
        validate_git_sha(sources[key], _path(sources_path, key))

    images_path = "$.images"
    images = record["images"]
    if not isinstance(images, dict):
        raise ValidationError(images_path, "expected object")
    for name, image in images.items():
        name_path = _path(images_path, str(name))
        validate_name(name, name_path)
        validate_image_ref(image, name_path)

    validate_digest(record["bundle_sha256"], "$.bundle_sha256")
    rollback_floor = validate_nonnegative_integer(record["rollback_floor"], "$.rollback_floor")
    if rollback_floor > sequence:
        raise ValidationError("$.rollback_floor", "must be at most sequence")
    return record


def validate_probe_result(value: Any) -> dict[str, Any]:
    """Validate and return a ProbeResult mapping."""
    result = _object(value, "$", PROBE_RESULT_FIELDS)
    _schema_one(result["schema"], "$.schema")
    validate_probe_id(result["probe"], "$.probe")
    validate_name(result["target"], "$.target")
    _nullable(result["release_sequence"], "$.release_sequence", validate_sequence)
    _nullable(result["observed_image_id"], "$.observed_image_id", validate_digest)
    if not isinstance(result["result"], str) or result["result"] not in {"PASS", "FAIL", "ERROR"}:
        raise ValidationError("$.result", "expected PASS, FAIL, or ERROR")
    validate_timestamp(result["observed_at"], "$.observed_at")
    validate_nonnegative_integer(result["duration_ms"], "$.duration_ms")
    detail = result["detail"]
    if not isinstance(detail, str) or len(detail) > 1024:
        raise ValidationError("$.detail", "expected string of at most 1024 characters")
    return result


def _string_array(value: Any, path: str, validator) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError(path, "expected array")
    result = []
    for index, item in enumerate(value):
        result.append(validator(item, _array_path(path, index)))
    return result


def _cycle_errors(applications: list[dict[str, Any]]) -> list[ValidationError]:
    by_name = {application["name"]: application for application in applications}
    paths = {application["name"]: index for index, application in enumerate(applications)}
    state: dict[str, int] = {}
    stack: list[str] = []
    found: set[tuple[str, ...]] = set()
    errors: list[ValidationError] = []

    def visit(name: str) -> None:
        state[name] = 1
        stack.append(name)
        for dependency in by_name[name]["depends_on"]:
            if dependency not in by_name:
                continue
            if state.get(dependency, 0) == 0:
                visit(dependency)
            elif state.get(dependency) == 1:
                start = stack.index(dependency)
                cycle = tuple(stack[start:] + [dependency])
                key = min(cycle[i:-1] + cycle[:i] for i in range(len(cycle) - 1))
                if key not in found:
                    found.add(key)
                    index = paths[name]
                    errors.append(ValidationError(
                        f"$.applications[{index}].depends_on",
                        f"dependency cycle: {' -> '.join(cycle)}",
                    ))
        stack.pop()
        state[name] = 2

    for application in applications:
        if state.get(application["name"], 0) == 0:
            visit(application["name"])
    return errors


def _policy_values(site_policy: Mapping[str, Any] | None) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not site_policy:
        return {}, {}
    rollback = site_policy.get("minimum_rollback", site_policy.get("min_rollback", site_policy.get("rollback", {})))
    probes = site_policy.get("required_probes", {})
    if not isinstance(rollback, Mapping) or not isinstance(probes, Mapping):
        raise ValidationError("$.site_policy", "expected minimum_rollback and required_probes mappings")
    return rollback, probes


def validate_application_manifest(
    value: Any,
    *,
    installed_probes: set[str] | frozenset[str] | None = None,
    gpu_profiles: set[str] | frozenset[str] | None = None,
    site_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate an ApplicationManifest and its site-specific semantics."""
    manifest = _object(value, "$", MANIFEST_FIELDS)
    if manifest["schema"] != 1 or isinstance(manifest["schema"], bool):
        raise ValidationError("$.schema", "unsupported schema version; expected 1")
    applications = manifest["applications"]
    if "site_id" in manifest:
        validate_name(manifest["site_id"], "$.site_id")
    if not isinstance(applications, list):
        raise ValidationError("$.applications", "expected array")

    names: set[str] = set()
    for index, raw_application in enumerate(applications):
        path = _array_path("$.applications", index)
        application = _object(raw_application, path, APPLICATION_FIELDS)
        name = validate_name(application["name"], _path(path, "name"))
        if name in names:
            raise ValidationError(_path(path, "name"), "duplicate application name")
        names.add(name)
        validate_image_ref(application["image"], _path(path, "image"))
        rollback = application["rollback"]
        if not isinstance(rollback, str) or rollback not in {"automatic", "manual"}:
            raise ValidationError(_path(path, "rollback"), "expected automatic or manual")
        _string_array(application["depends_on"], _path(path, "depends_on"), validate_name)
        _string_array(application["secrets"], _path(path, "secrets"), validate_secret_name)
        validate_probe_id(application["health_probe"], _path(path, "health_probe"))
        semantic = _string_array(application["semantic_probes"], _path(path, "semantic_probes"), validate_probe_id)
        if not semantic:
            raise ValidationError(_path(path, "semantic_probes"), "must contain at least one probe")
        if "gpu" in application:
            gpu_path = _path(path, "gpu")
            gpu = _object(application["gpu"], gpu_path, {"profile": (True, "Name")})
            validate_name(gpu["profile"], _path(gpu_path, "profile"))

    enabled = names
    errors: list[ValidationError] = []
    for index, application in enumerate(applications):
        for dep_index, dependency in enumerate(application["depends_on"]):
            if dependency not in enabled:
                errors.append(ValidationError(
                    f"$.applications[{index}].depends_on[{dep_index}]",
                    "dependency is not an enabled application",
                ))

    errors.extend(_cycle_errors(applications))
    rollback_policy, required_policy = _policy_values(site_policy)
    for index, application in enumerate(applications):
        path = _array_path("$.applications", index)
        probes = [application["health_probe"], *application["semantic_probes"]]
        if installed_probes is not None:
            for probe in probes:
                if probe not in installed_probes:
                    errors.append(ValidationError(
                        _path(path, "health_probe" if probe == application["health_probe"] else "semantic_probes"),
                        f"probe is not installed: {probe}",
                    ))
        if "gpu" in application and gpu_profiles is not None:
            profile = application["gpu"]["profile"]
            if profile not in gpu_profiles:
                errors.append(ValidationError(
                    _path(path, "gpu.profile"), f"GPU profile is not installed: {profile}"
                ))
        minimum = rollback_policy.get(application["name"])
        if minimum == "manual" and application["rollback"] == "automatic":
            errors.append(ValidationError(
                _path(path, "rollback"), "automatic rollback is below the site policy minimum manual"
            ))
        required = required_policy.get(application["name"], ())
        if not isinstance(required, (list, tuple, set, frozenset)):
            raise ValidationError(
                f"$.site_policy.required_probes.{application['name']}", "expected probe ID array"
            )
        for probe in required:
            if probe not in application["semantic_probes"]:
                errors.append(ValidationError(
                    _path(path, "semantic_probes"), f"missing policy-required probe: {probe}"
                ))
    if errors:
        raise ValidationErrors(errors)
    return manifest


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("$", f"duplicate object key {key!r}")
        result[key] = value
    return result


def parse_json(text: str | bytes) -> Any:
    """Parse JSON while rejecting duplicate object keys."""
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except ValidationError:
        raise
    except json.JSONDecodeError as exc:
        raise ValidationError("$", f"invalid JSON: {exc.msg}") from exc


def load_application_manifest(
    source: str | bytes,
    *,
    yaml: bool = False,
    installed_probes: set[str] | frozenset[str] | None = None,
    gpu_profiles: set[str] | frozenset[str] | None = None,
    site_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse and validate JSON, or safe-load YAML when ``yaml=True``."""
    if yaml:
        try:
            import yaml as yaml_module
            value = yaml_module.safe_load(source)
        except ImportError as exc:
            raise RuntimeError("PyYAML is required for YAML manifests") from exc
        except Exception as exc:
            raise ValidationError("$", f"invalid YAML: {exc}") from exc
    else:
        value = parse_json(source)
    return validate_application_manifest(
        value,
        installed_probes=installed_probes,
        gpu_profiles=gpu_profiles,
        site_policy=site_policy,
    )


def _load_contract(source: str | bytes, validator, *, yaml: bool = False) -> dict[str, Any]:
    if yaml:
        try:
            import yaml as yaml_module
            value = yaml_module.safe_load(source)
        except ImportError as exc:
            raise RuntimeError("PyYAML is required for YAML contracts") from exc
        except Exception as exc:
            raise ValidationError("$", f"invalid YAML: {exc}") from exc
    else:
        value = parse_json(source)
    return validator(value)


def load_release_record(source: str | bytes, *, yaml: bool = False) -> dict[str, Any]:
    """Parse and validate a ReleaseRecord."""
    return _load_contract(source, validate_release_record, yaml=yaml)


def load_probe_result(source: str | bytes, *, yaml: bool = False) -> dict[str, Any]:
    """Parse and validate a ProbeResult."""
    return _load_contract(source, validate_probe_result, yaml=yaml)


# Friendly aliases for callers that use the contract names directly.
validate_manifest = validate_application_manifest
parse_manifest = load_application_manifest
