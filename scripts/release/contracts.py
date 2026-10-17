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
def _path(path: str, key: str) -> str:
    return f"{path}.{key}" if path != "$" else f"$.{key}"


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


def validate_timestamp(value: Any, path: str = "$") -> str:
    if not isinstance(value, str):
        raise ValidationError(path, "expected Timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError(path, "expected UTC RFC3339 Timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValidationError(path, "expected UTC RFC3339 Timestamp")
    if "T" not in value or not (value.endswith("Z") or value[-6] in "+-"):
        raise ValidationError(path, "expected UTC RFC3339 Timestamp")
    return value


def validate_sequence(value: Any, path: str = "$") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63 - 1:
        raise ValidationError(path, "expected Sequence")
    return value


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


def validate_release_record(value: Any) -> dict[str, Any]:
    """Validate and return a ReleaseRecord mapping."""
    record = _object(value, "$", RELEASE_RECORD_FIELDS)
    _schema_one(record["schema"], "$.schema")
    validate_name(record["site_id"], "$.site_id")
    sequence = validate_sequence(record["sequence"], "$.sequence")
    validate_git_sha(record["tooling_git_sha"], "$.tooling_git_sha")

    sources_path = "$.sources"
    sources = record["sources"]
    if not isinstance(sources, dict):
        raise ValidationError(sources_path, "expected object")
    for required in ("homelab", "homelab-site"):
        if required not in sources:
            raise ValidationError(f"{sources_path}.{required}", "missing required source")
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
