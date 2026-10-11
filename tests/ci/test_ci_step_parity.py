from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED = (
    "Workflow security policy",
    "Release pytest",
    "Compose config with synthetic secrets",
    "Compose shell tests",
    "Ops shell tests",
    "Package pytest",
    "MCP pytest",
    "Package ruff",
    "Homelab agent pytest",
    "Homelab agent ruff",
    "Media agent pytest",
    "Media agent ruff",
    "Qwen code Telegram tests",
    "Public readiness",
    "Bash syntax",
    "README mermaid blocks",
)


def test_validate_step_names_match_between_workflow_and_local_gate() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    validate = workflow.split("\n  gitleaks:", 1)[0]
    workflow_names = tuple(re.findall(r"^      - name: (.+)$", validate, re.MULTILINE))
    local = (ROOT / "scripts/ci/ci-local.sh").read_text(encoding="utf-8")
    local_names = tuple(re.findall(r'^  run_step "([^"]+)"', local, re.MULTILINE))

    assert set(workflow_names) >= set(EXPECTED)
    assert set(local_names) >= set(EXPECTED)
