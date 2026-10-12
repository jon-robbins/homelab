"""CAS publication of signed release records."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .contracts import load_release_record
from .bundle import bundle_sha256
from .sign import record_bytes, sign as environment_sign


class FeedError(ValueError):
    pass


class RefConflict(FeedError):
    """The observed branch ref changed before the commit was accepted."""


class Publication:
    def __init__(self, sequence: int | None, commit_sha: str | None, noop: bool) -> None:
        self.sequence, self.commit_sha, self.noop = sequence, commit_sha, noop


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
    return load_release_record(data)


def publish(client: Any, record: Mapping[str, Any], bundle: bytes, *,
            repository: str = "homelab/homelab-site", branch: str = "releases",
            signer: Any = environment_sign, max_retries: int = 8) -> Publication:
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
    value = load_release_record(latest if isinstance(latest, bytes) else latest.encode())
    return value["sequence"] + 1


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
    """Allocate ``last + 1``, sign the resulting record, and publish it."""
    next_record = dict(record)
    next_record["sequence"] = allocate_sequence(client, repository=repository, branch=branch)
    record_data, signature = sign(next_record)
    if not isinstance(record_data, bytes) or not isinstance(signature, bytes):
        raise FeedError("signer must return record bytes and signature bytes")
    return publish_feed(client, next_record, signature, bundle, repository=repository, branch=branch, max_retries=max_retries)


def publish_feed(client: Any, record: Mapping[str, Any], signature: bytes, bundle: bytes, *, repository: str = "homelab-site", branch: str = "releases", max_retries: int = 8) -> str:
    """Append one release using an observed-ref CAS; never force-pushes."""
    record_data = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    load_release_record(record_data)
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
            latest_record = load_release_record(latest if isinstance(latest, bytes) else latest.encode())
            if record["sequence"] <= latest_record["sequence"]:
                raise FeedError("sequence is not strictly greater than published sequence")
        files = {f"records/{sequence}/record.json": record_data, f"records/{sequence}/record.sig": signature, f"records/{sequence}/bundle.tar": bundle}
        try:
            _call(client, ("commit", "create_commit"), repository, branch, ref, files, f"release: publish {sequence}")
            return "published"
        except (RuntimeError, ValueError) as error:
            if "changed" not in str(error).lower() and "cas" not in str(error).lower():
                raise
    raise FeedError("release feed changed too many times")


publish = publish_feed
