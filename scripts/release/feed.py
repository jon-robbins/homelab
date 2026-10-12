"""CAS publication of signed release records."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .contracts import load_release_record
from .bundle import bundle_sha256
from .sign import parse_signature, record_bytes, sign as environment_sign, verify_signature


class FeedError(ValueError):
    pass


class RefConflict(FeedError):
    """The observed branch ref changed before the commit was accepted."""


class Publication:
    def __init__(self, sequence: int | None, commit_sha: str | None, noop: bool) -> None:
        self.sequence, self.commit_sha, self.noop = sequence, commit_sha, noop


def _verify_release_signature(record_data: bytes, signature: bytes, public_keys: Any) -> None:
    """Verify the strict key-id envelope before interpreting the record."""
    try:
        key_id, raw = parse_signature(signature)
    except ValueError as error:
        raise FeedError("malformed release signature") from error
    if not isinstance(public_keys, Mapping) or key_id not in public_keys:
        raise FeedError(f"unknown release signing key ID: {key_id}")
    if not verify_signature(record_data, raw, public_keys[key_id]):
        raise FeedError("signature does not verify canonical record")


def _record(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (bytes, bytearray, str)):
        return load_release_record(value)
    raise FeedError("client returned an invalid release record")


def _previous(client: Any, repository: str, branch: str, ref: str) -> Mapping[str, Any] | None:
    latest = getattr(client, "latest_record", None)
    if latest is not None:
        value = latest(repository, branch, ref)
        if value is None:
            return None
        return value[1] if isinstance(value, tuple) else value
    sequences = client.record_sequences(repository, branch, ref)
    if not sequences:
        return None
    sequence = max(int(item) for item in sequences)
    data = client.read_file(repository, f"records/{sequence}/record.json", ref)
    if data is None:
        raise FeedError(f"record sequence {sequence} has no record.json")
    return _record(data)


def publish(client: Any, record: Mapping[str, Any], bundle: bytes, *,
            repository: str = "homelab/homelab-site", branch: str = "releases",
            signer: Any = environment_sign, public_key: bytes | Any | None = None,
            max_retries: int = 8) -> Publication:
    """Publish through the small ``branch_ref/read_file/commit_files`` interface."""
    if record.get("bundle_sha256") != bundle_sha256(bundle):
        raise FeedError("record bundle_sha256 does not match bundle bytes")
    for _ in range(max_retries):
        ref = client.branch_ref(repository, branch)
        previous = _previous(client, repository, branch, ref)
        if previous is not None:
            previous = previous[1] if isinstance(previous, tuple) else previous
            fields = ("schema", "site_id", "tooling_git_sha", "sources", "images", "bundle_sha256", "rollback_floor")
            if all(previous.get(field) == record.get(field) for field in fields):
                return Publication(previous["sequence"], None, True)
        candidate = dict(record)
        candidate["sequence"] = (previous["sequence"] + 1) if previous else 1
        data = record_bytes(candidate)
        signature = signer(candidate)
        if public_key is None:
            raise FeedError("publishing requires a public key")
        _verify_release_signature(data, signature, public_key)
        path = f"records/{candidate['sequence']}"
        existing = client.read_file(repository, f"{path}/record.json", ref)
        if existing is not None:
            if existing != data:
                raise FeedError(f"sequence {candidate['sequence']} already exists with different bytes")
            return Publication(candidate["sequence"], None, True)
        files = {f"{path}/record.json": data, f"{path}/record.sig": signature, f"{path}/bundle.tar": bytes(bundle)}
        try:
            commit_sha = client.commit_files(repository, branch, ref, files, f"release: publish sequence {candidate['sequence']}")
        except RefConflict:
            continue
        if not commit_sha:
            raise FeedError("client did not return commit SHA")
        return Publication(candidate["sequence"], commit_sha, False)
    raise FeedError("release feed changed too often to publish safely")


def _call(client: Any, names: tuple[str, ...], *args: Any, **kwargs: Any) -> Any:
    for name in names:
        method = getattr(client, name, None)
        if method is not None:
            return method(*args, **kwargs)
    raise TypeError(f"client lacks {names[0]}()")


def allocate_sequence(client: Any, *, repository: str = "homelab-site", branch: str = "releases") -> int:
    """Read the published feed and allocate its next sequence number."""
    latest = _call(client, ("last_record",), repository, branch)
    if latest is None:
        return 1
    value = _record(latest)
    if value is None:
        raise FeedError("client returned no release record")
    return value["sequence"] + 1


def publish_next(
    client: Any,
    record: Mapping[str, Any],
    sign: Any,
    bundle: bytes,
    *,
    repository: str = "homelab-site",
    branch: str = "releases",
    public_key: bytes | Any,
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
        record_data, signature = sign(next_record)
        if not isinstance(record_data, bytes) or not isinstance(signature, bytes):
            raise FeedError("signer must return record bytes and signature bytes")
        canonical_data = record_bytes(next_record)
        try:
            signed_record = load_release_record(record_data)
        except (TypeError, ValueError) as error:
            raise FeedError("signer returned invalid record bytes") from error
        if signed_record != next_record or record_data != canonical_data:
            raise FeedError("signer returned record bytes different from the canonical record")
        _verify_release_signature(record_data, signature, public_key)
        path = f"records/{next_record['sequence']}"
        existing = _call(client, ("read", "read_file"), repository, branch,
                          f"{path}/record.json", allow_missing=True)
        if existing is not None:
            existing_bytes = existing if isinstance(existing, bytes) else existing.encode()
            if existing_bytes != record_data:
                raise FeedError(f"sequence {next_record['sequence']} already contains different record bytes")
            return "noop"
        files = {f"{path}/record.json": record_data, f"{path}/record.sig": signature,
                 f"{path}/bundle.tar": bytes(bundle)}
        try:
            _call(client, ("commit_files",), repository, branch, ref, files,
                  f"release: publish {next_record['sequence']}")
            return "published"
        except (RuntimeError, ValueError) as error:
            if "changed" not in str(error).lower() and "cas" not in str(error).lower():
                raise
    raise FeedError("release feed changed too many times")


def publish_feed(client: Any, record: Mapping[str, Any], signature: bytes, bundle: bytes, *,
                 repository: str = "homelab-site", branch: str = "releases",
                 public_key: bytes | Any | None = None, max_retries: int = 8) -> str:
    """Append one release using an observed-ref CAS; never force-pushes."""
    if record.get("bundle_sha256") != bundle_sha256(bundle):
        raise FeedError("record bundle_sha256 does not match bundle bytes")
    record_data = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    load_release_record(record_data)
    if public_key is None:
        raise FeedError("publishing requires a public key")
    _verify_release_signature(record_data, signature, public_key)
    sequence = record["sequence"]
    for _ in range(max_retries):
        ref = _call(client, ("ref", "get_ref"), repository, branch)
        old_record = _call(client, ("read", "read_file"), repository, branch, f"records/{sequence}/record.json", allow_missing=True)
        if old_record is not None:
            old_bytes = old_record if isinstance(old_record, bytes) else old_record.encode()
            if old_bytes != record_data:
                raise FeedError(f"sequence {sequence} already contains different record bytes")
            return "noop"
        latest = _call(client, ("last_record",), repository, branch)
        if latest is not None:
            latest_record = _record(latest)
            if latest_record is None:
                raise FeedError("client returned no release record")
            if record["sequence"] <= latest_record["sequence"]:
                raise FeedError("sequence is not strictly greater than published sequence")
        files = {f"records/{sequence}/record.json": record_data, f"records/{sequence}/record.sig": signature, f"records/{sequence}/bundle.tar": bundle}
        try:
            _call(client, ("commit_files",), repository, branch, ref, files, f"release: publish {sequence}")
            return "published"
        except (RuntimeError, ValueError) as error:
            if "changed" not in str(error).lower() and "cas" not in str(error).lower():
                raise
    raise FeedError("release feed changed too many times")


publish = publish_feed
