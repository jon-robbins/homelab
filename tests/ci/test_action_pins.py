"""Repository policy for GitHub Actions workflows.

Pin shape (full commit SHA) and version comments are enforced by the pinact job
(ci.yml "Verify action pins", offline 40-character SHA check plus required
"# <version>" comments) and zizmor, and test_workflow_security.py asserts the
same pin regexes on every step. What those tools do not cover stays here: the
allowlist of action names and the nox session-list wiring in ci.yml.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
REMOTE_ACTION = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@(?P<ref>[^\s]+)$"
)
ALLOWED_ACTIONS = {
    "actions/checkout",
    "actions/setup-python",
    "actions/download-artifact",
    "actions/upload-artifact",
    "actions/attest-build-provenance",
    "actions/create-github-app-token",
    "dependabot/fetch-metadata",
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


def test_remote_actions_are_allowlisted() -> None:
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
    assert found


def test_ci_lists_nox_sessions_for_the_matrix() -> None:
    _, ci = next(
        (path, doc) for path, doc in _workflow_documents() if path.name == "ci.yml"
    )
    assert '"--list", "--json"' in ci["jobs"]["session-list"]["steps"][-1]["run"]
