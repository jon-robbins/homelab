"""CAS publication of signed release records."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Mapping

from .contracts import load_release_record
from .bundle import bundle_sha256
from .sign import COSIGN_OIDC_ISSUER, record_bytes, signing_identity


class FeedError(ValueError):
    pass


def verify_cosign_bundle(record_path: Path, bundle_path: Path, *, cosign: str = "cosign",
                        identity: str | None = None, issuer: str = COSIGN_OIDC_ISSUER,
                        offline: bool = False, trusted_root: Path | None = None) -> None:
    """Verify the exact record with the release workflow's GitHub identity."""
    if not record_path.is_file() or not bundle_path.is_file():
        raise FeedError("record or Sigstore bundle is missing")
    command = [cosign, "verify-blob", "--bundle", str(bundle_path),
               "--certificate-identity", identity or signing_identity(),
               "--certificate-oidc-issuer", issuer]
    if offline:
        command.append("--offline")
    if trusted_root is not None:
        command.extend(("--trusted-root", str(trusted_root)))
    command.append(str(record_path))
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "cosign verification failed").strip()
        raise FeedError(f"invalid Sigstore release signature: {detail[:300]}")


def _record(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (bytes, bytearray, str)):
        return load_release_record(value)
    raise FeedError("client returned an invalid release record")


def _call(client: Any, names: tuple[str, ...], *args: Any, **kwargs: Any) -> Any:
    for name in names:
        method = getattr(client, name, None)
        if method is not None:
            return method(*args, **kwargs)
    raise TypeError(f"client lacks {names[0]}()")


def publish_next(
    client: Any,
    record: Mapping[str, Any],
    sign: Any,
    bundle: bytes,
    *,
    repository: str = "homelab-site",
    branch: str = "releases",
    max_retries: int = 8,
) -> str:
    """Allocate, sign, and append; every CAS retry re-reads and re-signs."""
    if record.get("bundle_sha256") != bundle_sha256(bundle):
        raise FeedError("record bundle_sha256 does not match bundle bytes")
    for _ in range(max_retries):
        ref = _call(client, ("ref", "get_ref"), repository, branch)
        latest = _call(client, ("last_record",), repository, branch)
        latest_record = _record(latest)
        fields = ("schema", "site_id", "tooling_git_sha", "sources", "images", "bundle_sha256", "rollback_floor")
        if latest_record is not None and all(latest_record.get(field) == record.get(field) for field in fields):
            return "noop"
        next_record = dict(record)
        next_record["sequence"] = latest_record["sequence"] + 1 if latest_record else 1
        record_data, sigstore_bundle = sign(next_record)
        if not isinstance(record_data, bytes) or not isinstance(sigstore_bundle, bytes):
            raise FeedError("signer must return record bytes and Sigstore bundle bytes")
        canonical_data = record_bytes(next_record)
        try:
            signed_record = load_release_record(record_data)
        except (TypeError, ValueError) as error:
            raise FeedError("signer returned invalid record bytes") from error
        if signed_record != next_record or record_data != canonical_data:
            raise FeedError("signer returned record bytes different from the canonical record")
        path = f"records/{next_record['sequence']}"
        existing = _call(client, ("read", "read_file"), repository, branch,
                          f"{path}/record.json", allow_missing=True)
        if existing is not None:
            existing_bytes = existing if isinstance(existing, bytes) else existing.encode()
            if existing_bytes != record_data:
                raise FeedError(f"sequence {next_record['sequence']} already contains different record bytes")
            return "noop"
        files = {f"{path}/record.json": record_data, f"{path}/record.sigstore.json": sigstore_bundle,
                 f"{path}/bundle.tar": bytes(bundle)}
        try:
            _call(client, ("commit_files",), repository, branch, ref, files,
                  f"release: publish {next_record['sequence']}")
            return "published"
        except (RuntimeError, ValueError) as error:
            if "changed" not in str(error).lower() and "cas" not in str(error).lower():
                raise
    raise FeedError("release feed changed too many times")