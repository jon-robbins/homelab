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
from scripts.release.contracts import load_release_record


@dataclass(frozen=True)
class FeedPolicy:
    """The feed URL, the trust directory holding feed.token, and the host policy that verifies records.

    host_policy None skips the signature check; only tests do that, homelabctl always passes one.
    """

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


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env).stdout


def _auth_env(policy: FeedPolicy) -> dict[str, str]:
    env = os.environ.copy()
    token_file = Path(policy.trust_directory) / "feed.token"
    token = token_file.read_text().strip() if token_file.exists() else ""
    if token:
        header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env |= {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader"}
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {header}"
    return env


def fetch_newest(highwater: int, policy: FeedPolicy) -> Candidate | None:
    """Return the newest record above *highwater* as immutable bytes, verified when a host policy is given."""
    if isinstance(highwater, bool) or highwater < 0:
        raise ValueError("highwater must be nonnegative")
    with tempfile.TemporaryDirectory(prefix="homelab-feed-") as directory:
        root = Path(directory)
        subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
        ref = "refs/remotes/feed/releases"
        _git(
            root, "fetch", "--quiet", "--depth=1", policy.feed_url, f"refs/heads/releases:{ref}", env=_auth_env(policy)
        )
        paths = _git(root, "ls-tree", "-r", "--name-only", ref, "records").decode().splitlines()
        parts = [path.split("/") for path in paths]
        sequences = {int(p[1]) for p in parts if len(p) == 3 and p[2] == "record.json" and int(p[1]) > highwater}
        if not sequences:
            return None
        sequence = max(sequences)
        record_dir = root / "records" / str(sequence)
        record_dir.mkdir(parents=True)
        for name in ("record.json", "record.sigstore.json", "bundle.tar"):
            (record_dir / name).write_bytes(_git(root, "show", f"{ref}:records/{sequence}/{name}"))
        if policy.host_policy is not None:
            # Admission binds the record directory to the feed root; the fetched copy is temporary.
            record, raw, signature, _ = verify_record(record_dir, replace(policy.host_policy, feed_directory=str(root)))
        else:
            raw, signature = (
                (record_dir / "record.json").read_bytes(),
                (record_dir / "record.sigstore.json").read_bytes(),
            )
            record = load_release_record(raw)
        if record["sequence"] != sequence:
            raise ValueError(f"record sequence does not match records/{sequence}")
        bundle = (record_dir / "bundle.tar").read_bytes()
        verify_bundle(bundle)
        return Candidate(sequence, record, raw, signature, bundle)
