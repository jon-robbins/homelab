from __future__ import annotations

import re
from pathlib import Path


MCP_SOURCE = Path(__file__).resolve().parents[2] / "apps/homelab-mcp"
FORBIDDEN_IMPORT = re.compile(r"\b(?:from|import)\s+tracker_integrations\b")
FORBIDDEN_POLICY_NAMES = ("tracker_integrations", "tracker-integrations", "tracker_policy")


def test_homelab_mcp_has_no_private_tracker_integrations_or_policy_names() -> None:
    offenders: list[str] = []
    for path in MCP_SOURCE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if FORBIDDEN_IMPORT.search(text) or any(name in text for name in FORBIDDEN_POLICY_NAMES):
            offenders.append(str(path.relative_to(MCP_SOURCE)))
    assert offenders == []
