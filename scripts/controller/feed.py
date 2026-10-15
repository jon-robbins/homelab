"""Fetch signed release candidates from the site's git feed."""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from scripts.controller.admission import HostPolicy, verify_record
from scripts.release.bundle import verify as verify_bundle


@dataclass(frozen=True)
class FeedPolicy:
    """The feed endpoint and the directory containing its host credential."""

    feed_url: str
    trust_directory: Path
    host_policy: HostPolicy | None = None


@dataclass(frozen=True)
class Candidate:
    sequence: int
    record: dict[str, Any]
    record_bytes: bytes
    signature_bytes: bytes
    bundle_bytes: bytes


def _value(policy: Any, name: str, default: Any = None) -> Any:
    return getattr(policy, name, default)


def _feed_url(policy: Any) -> str:
    value = _value(policy, "feed_url") or _value(policy, "repository")
    if value is None and _value(policy, "feed_directory") is not None:
        value = _value(policy, "feed_directory")
    if not value:
        raise ValueError("host policy has no release feed URL")
    return str(value)


def _token(policy: Any) -> str | None:
    path = _value(policy, "feed_token")
    if path is None:
        trust = _value(policy, "trust_directory") or _value(policy, "trust")
        path = Path(trust) / "feed.token" if trust else None
    if path is None:
        return None
    token = Path(path).read_text().strip()
    return token or None


def fetch_newest(highwater: int, policy: HostPolicy | Any) -> Candidate | None:
    """Fetch ``releases`` and return the greatest sequence above *highwater*.

    The feed is copied to a temporary git repository, so the returned candidate
    consists only of immutable bytes and remains usable after this function.
    """
    if isinstance(highwater, bool) or highwater < 0:
        raise ValueError("highwater must be nonnegative")
    with tempfile.TemporaryDirectory(prefix="homelab-feed-") as directory:
        root = Path(directory)
        subprocess.run(["git", "init", "--quiet", str(root)], check=True,
                       capture_output=True, text=True)
        env = os.environ.copy()
        token = _token(policy)
        if token:
            credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credentials}"})
        subprocess.run(["git", "-C", str(root), "fetch", "--quiet", "--depth=1",
                        _feed_url(policy), "refs/heads/releases:refs/remotes/feed/releases"],
                       check=True, capture_output=True, text=True, env=env)
        tree = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "--name-only",
                               "refs/remotes/feed/releases", "records"], check=True,
                              capture_output=True, text=True).stdout.splitlines()
        sequences = {int(parts[1]) for path in tree if (parts := path.split("/")) and
                     len(parts) == 3 and parts[0] == "records" and parts[2] == "record.json"}
        sequences = {sequence for sequence in sequences if sequence > highwater}
        if not sequences:
            return None
        sequence = max(sequences)
        record_dir = root / "records" / str(sequence)
        record_dir.mkdir(parents=True)
        for name in ("record.json", "record.sigstore.json", "bundle.tar"):
            output = subprocess.run(["git", "-C", str(root), "show",
                                     f"refs/remotes/feed/releases:records/{sequence}/{name}"],
                                    check=True, capture_output=True)
            (record_dir / name).write_bytes(output.stdout)
        host_policy = _value(policy, "host_policy")
        if host_policy is None and isinstance(policy, HostPolicy):
            host_policy = policy
        if host_policy is not None:
            # Admission constrains the record directory to its feed root.  The
            # fetched snapshot is temporary, so bind that check to this root.
            record, raw, signature, _ = verify_record(
                record_dir, replace(host_policy, feed_directory=root))
        else:
            from scripts.release.contracts import load_release_record
            raw = (record_dir / "record.json").read_bytes()
            record = load_release_record(raw)
            signature = (record_dir / "record.sigstore.json").read_bytes()
        if record["sequence"] != sequence:
            raise ValueError(f"record sequence does not match records/{sequence}")
        bundle = (record_dir / "bundle.tar").read_bytes()
        verify_bundle(bundle)
        return Candidate(sequence, record, raw, signature, bundle)
