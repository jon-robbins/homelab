"""Canonical ReleaseRecord serialization and Sigstore cosign helpers."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .contracts import validate_release_record

COSIGN_VERSION = "v3.1.3"
COSIGN_IDENTITY_TEMPLATE = "https://github.com/{repository}/.github/workflows/release.yml@refs/heads/main"
COSIGN_OIDC_ISSUER = "https://token.actions.githubusercontent.com"


def record_bytes(record: Mapping[str, Any]) -> bytes:
    validate_release_record(dict(record))
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()


def signing_identity(repository: str | None = None) -> str:
    repository = repository or os.environ.get("GITHUB_REPOSITORY", "jon-robbins/homelab")
    if not repository or repository.startswith("http") or "/" not in repository:
        raise ValueError("GITHUB_REPOSITORY must be owner/name")
    return COSIGN_IDENTITY_TEMPLATE.format(repository=repository)


def sign_record(record: Mapping[str, Any], bundle_path: Path, *, cosign: str = "cosign") -> bytes:
    """Write a keyless Sigstore bundle and return the canonical record bytes."""
    data = record_bytes(record)
    record_path = bundle_path.with_name("record.json")
    record_path.write_bytes(data)
    completed = subprocess.run([cosign, "sign-blob", "--yes", "--bundle", str(bundle_path), str(record_path)], capture_output=True, text=True)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "cosign signing failed").strip()
        raise ValueError(f"cosign signing failed: {detail[:300]}")
    if not bundle_path.is_file():
        raise ValueError("cosign did not create a bundle")
    return data
