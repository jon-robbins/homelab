"""Host policy and offline signature verification for release records."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import tempfile
from typing import Any, Callable

from scripts.release.bundle import bundle_sha256, verify as verify_bundle
from scripts.release.contracts import load_release_record, parse_json
from scripts.release.feed import verify_cosign_bundle


@dataclass(frozen=True)
class HostPolicy:
    site_id: str
    signer_identity: str
    oidc_issuer: str
    trusted_root: Path
    feed_directory: Path


def load_host_policy(path: Path | str) -> HostPolicy:
    """Load schema 1 host policy, rejecting unknown or missing keys."""
    try:
        value = parse_json(Path(path).read_bytes())
    except (OSError, ValueError) as error:
        raise ValueError("invalid host policy") from error
    fields = {"schema", "site_id", "signer_identity", "oidc_issuer", "trusted_root", "feed_directory"}
    if not isinstance(value, dict) or set(value) != fields or value.get("schema") != 1 or isinstance(value.get("schema"), bool):
        raise ValueError("host policy must contain exactly the schema 1 fields")
    for key in fields - {"schema"}:
        if not isinstance(value[key], str) or not value[key]:
            raise ValueError(f"invalid host policy field: {key}")
    return HostPolicy(value["site_id"], value["signer_identity"], value["oidc_issuer"],
                      Path(value["trusted_root"]), Path(value["feed_directory"]))


def verify_record(record_dir: Path | str, policy: HostPolicy | Path | str,
                  verifier: Callable[..., None] = verify_cosign_bundle
                  ) -> tuple[dict[str, Any], bytes, bytes, str]:
    """Verify exact record bytes offline, then parse and check the host site."""
    policy = policy if isinstance(policy, HostPolicy) else load_host_policy(policy)
    directory = Path(record_dir)
    if not directory.resolve().is_relative_to(policy.feed_directory.resolve()):
        raise ValueError("record directory is outside the configured feed directory")
    record_path = directory / "record.json"
    signature_path = directory / "record.sigstore.json"
    bundle_path = directory / "bundle.tar"
    record_bytes = record_path.read_bytes()
    signature_bytes = signature_path.read_bytes()
    bundle_bytes = bundle_path.read_bytes()
    if not policy.trusted_root.is_file():
        raise ValueError("cached Sigstore trusted root is missing")
    with tempfile.TemporaryDirectory(prefix="release-record-") as snapshot:
        snapshot_dir = Path(snapshot)
        snapshot_record = snapshot_dir / "record.json"
        snapshot_signature = snapshot_dir / "record.sigstore.json"
        snapshot_bundle = snapshot_dir / "bundle.tar"
        snapshot_record.write_bytes(record_bytes)
        snapshot_signature.write_bytes(signature_bytes)
        snapshot_bundle.write_bytes(bundle_bytes)
        verifier(snapshot_record, snapshot_signature, identity=policy.signer_identity,
                 issuer=policy.oidc_issuer, offline=True, trusted_root=policy.trusted_root)
        verify_bundle(bundle_bytes)
    record = load_release_record(record_bytes)
    if record["site_id"] != policy.site_id:
        raise ValueError("release record site_id does not match host policy")
    if record["bundle_sha256"] != bundle_sha256(bundle_bytes):
        raise ValueError("release record bundle_sha256 does not match bundle")
    return record, record_bytes, signature_bytes, hashlib.sha256(record_bytes).hexdigest()
