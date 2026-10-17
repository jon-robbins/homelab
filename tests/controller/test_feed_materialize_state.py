import base64
import fcntl
import io
import json
import subprocess
import tarfile

import pytest

from scripts.controller.feed import FeedPolicy, fetch_newest
from scripts.controller.materialize import materialize
from scripts.controller.state import State
from scripts.release.bundle import build, bundle_sha256


def _run(*command):
    return subprocess.run(command, check=True, capture_output=True, text=True)


def _bundle(tmp_path, name="source"):
    source = tmp_path / name
    (source / "files").mkdir(parents=True)
    for filename in ("applications.json", "compose.yaml", "probe-targets.json"):
        (source / filename).write_bytes(b"{}")
    return build(source)


def _record(sequence, bundle):
    return {
        "schema": 1,
        "site_id": "test-site",
        "sequence": sequence,
        "tooling_git_sha": "1" * 40,
        "sources": {"homelab": "2" * 40, "homelab-site": "3" * 40},
        "images": {},
        "bundle_sha256": bundle_sha256(bundle),
        "rollback_floor": 0,
    }


def _publish(worktree, bare, sequence, bundle, record=None):
    directory = worktree / "records" / str(sequence)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "record.json").write_text(
        json.dumps(record or _record(sequence, bundle), separators=(",", ":"))
    )
    (directory / "record.sigstore.json").write_bytes(b"test signature")
    (directory / "bundle.tar").write_bytes(bundle)
    _run("git", "-C", str(worktree), "add", "records")
    _run("git", "-C", str(worktree), "commit", "--quiet", "-m", str(sequence))
    _run("git", "-C", str(worktree), "push", "--quiet", str(bare), "HEAD:refs/heads/releases")


@pytest.fixture
def feed_repo(tmp_path):
    bare = tmp_path / "feed.git"
    worktree = tmp_path / "feed"
    trust = tmp_path / "trust"
    trust.mkdir()
    (trust / "feed.token").write_text("read-token\n")
    _run("git", "init", "--quiet", "--bare", str(bare))
    _run("git", "init", "--quiet", str(worktree))
    _run("git", "-C", str(worktree), "config", "user.email", "test@example.invalid")
    _run("git", "-C", str(worktree), "config", "user.name", "Test")
    bundle = _bundle(tmp_path)
    _publish(worktree, bare, 1, bundle)
    _publish(worktree, bare, 3, bundle)
    return FeedPolicy(str(bare), trust), worktree, bare, bundle


def test_fetch_newest_uses_local_feed_and_skips_older_records(feed_repo):
    policy, _, _, _ = feed_repo

    candidate = fetch_newest(1, policy)

    assert candidate is not None
    assert candidate.sequence == 3
    assert candidate.record["sequence"] == 3


def test_fetch_newest_rejects_v1_bundle(feed_repo):
    policy, worktree, bare, _ = feed_repo
    old_bundle = io.BytesIO()
    with tarfile.open(fileobj=old_bundle, mode="w") as archive:
        data = b"{}"
        member = tarfile.TarInfo("app.json")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    legacy = old_bundle.getvalue()
    _publish(worktree, bare, 4, legacy)

    with pytest.raises(ValueError, match="unexpected archive member"):
        fetch_newest(3, policy)


def test_fetch_newest_rejects_record_sequence_mismatch(feed_repo):
    policy, worktree, bare, bundle = feed_repo
    _publish(worktree, bare, 4, bundle, record=_record(5, bundle))

    with pytest.raises(ValueError, match="record sequence"):
        fetch_newest(3, policy)


def test_fetch_newest_configures_basic_feed_auth(feed_repo, monkeypatch):
    policy, _, _, _ = feed_repo
    original_run = subprocess.run
    fetch_environment = {}

    def run(*command, **kwargs):
        if command[0][0] == "git" and "fetch" in command[0]:
            fetch_environment.update(kwargs["env"])
        return original_run(*command, **kwargs)

    monkeypatch.setattr("scripts.controller.feed.subprocess.run", run)
    fetch_newest(1, policy)

    expected = base64.b64encode(b"x-access-token:read-token").decode()
    assert fetch_environment["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {expected}"


def test_materialize_publishes_read_only_release(tmp_path, feed_repo):
    policy, _, _, _ = feed_repo
    candidate = fetch_newest(1, policy)
    destination = materialize(candidate, tmp_path / "releases")

    assert destination.is_dir()
    assert destination.stat().st_mode & 0o777 == 0o555
    assert (destination / "applications.json").stat().st_mode & 0o777 == 0o444


def test_materialize_exposes_controller_models_at_release_root(tmp_path):
    source = tmp_path / "source"
    (source / "files").mkdir(parents=True)
    for filename in ("applications.json", "compose.yaml", "probe-targets.json"):
        (source / filename).write_text("{}")
    for filename in ("app.json", "edge.json", "site.yaml"):
        (source / "files" / filename).write_text(filename)
    candidate = type("Candidate", (), {"sequence": 1, "bundle_bytes": build(source)})()

    destination = materialize(candidate, tmp_path / "releases")

    for filename in ("app.json", "edge.json", "site.yaml"):
        assert (destination / filename).read_text() == filename


def test_interrupted_state_write_preserves_old_value(tmp_path, monkeypatch):
    state = State(tmp_path / "state")
    state.write("highwater", "7\n")

    def interrupted(*_args):
        raise OSError("interrupted before rename")

    monkeypatch.setattr("scripts.controller.state.os.replace", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        state.write("highwater", "8\n")

    assert state.read("highwater") == "7\n"


def test_state_operations_reuse_existing_lock(tmp_path, monkeypatch):
    state = State(tmp_path / "state")
    state.write("applying", "3\n")
    flock = fcntl.flock
    acquisitions = 0

    def count_acquisitions(fd, operation):
        nonlocal acquisitions
        if operation == fcntl.LOCK_EX:
            acquisitions += 1
            assert acquisitions == 1, "state operation acquired a second lock"
        return flock(fd, operation)

    monkeypatch.setattr("scripts.controller.state.fcntl.flock", count_acquisitions)
    with state.lock():
        state.set_highwater(8)
        state.remove("applying")

    assert state.highwater() == 8
    assert state.read("applying") is None
