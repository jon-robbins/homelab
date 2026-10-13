"""Repository policy for GitHub Actions workflows."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REMOTE_ACTION = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@(?P<ref>[^\s]+)$"
)
ALLOWED_ACTIONS = {
    "actions/checkout",
    "actions/setup-python",
    "actions/download-artifact",
    "actions/upload-artifact",
    "actions/attest-build-provenance",
    "dorny/paths-filter",
    "docker/setup-buildx-action",
    "sigstore/cosign-installer",
}


def _walk(value: object):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)
    elif isinstance(value, str):
        yield value


def _workflow_documents():
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        yield path, yaml.safe_load(path.read_text(encoding="utf-8"))


def _action_documents():
    yield from _workflow_documents()
    for path in sorted((ROOT / ".github" / "actions").glob("**/action.y*ml")):
        yield path, yaml.safe_load(path.read_text(encoding="utf-8"))


def test_remote_actions_are_allowlisted_and_pinned() -> None:
    found = 0
    for path, document in _action_documents():
        for value in _walk(document):
            if "/" not in value or "@" not in value:
                continue
            match = REMOTE_ACTION.fullmatch(value)
            if not match:
                continue
            found += 1
            assert match.group("name") in ALLOWED_ACTIONS, (path, value)
            assert FULL_SHA.fullmatch(match.group("ref")), (path, value)
    assert found


def test_workflow_actions_have_pin_comments() -> None:
    for path in sorted([*WORKFLOWS.glob("*.y*ml"), *(ROOT / ".github" / "actions").glob("**/action.y*ml")]):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "uses:" not in line or "@" not in line or "./" in line:
                continue
            assert re.search(r"# v\d+\.\d+\.\d+", line), (path, line)


def test_workflow_permissions_are_read_only() -> None:
    for path, document in _workflow_documents():
        assert document["permissions"] == {"contents": "read"}, path
        for job in document["jobs"].values():
            if isinstance(job, dict) and "permissions" in job:
                assert all(
                    value in {"read", "none", "write"}
                    for value in job["permissions"].values()
                ), path


def test_ci_required_jobs_and_nox_matrix_exist() -> None:
    _, ci = next(
        (path, doc) for path, doc in _workflow_documents() if path.name == "ci.yml"
    )
    assert {
        "validate",
        "gitleaks",
        "public-readiness",
        "image-builds",
        "pre-commit",
        "nox-sessions",
    } <= set(ci["jobs"])
    assert ci["jobs"]["nox-sessions"]["needs"] == "session-list"
    assert '"--list", "--json"' in ci["jobs"]["session-list"]["steps"][-1]["run"]
