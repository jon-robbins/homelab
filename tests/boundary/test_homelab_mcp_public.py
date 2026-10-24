from __future__ import annotations

import re
from pathlib import Path


MCP_SOURCE = Path(__file__).resolve().parents[2] / "apps/homelab-mcp"
RETIRED_EXTENSION = "tracker" + "_integrations"
FORBIDDEN_IMPORT = re.compile(r"\b(?:from|import)\s+" + re.escape(RETIRED_EXTENSION) + r"\b")
FORBIDDEN_POLICY_NAMES = (
    RETIRED_EXTENSION,
    "tracker" + "-integrations",
    "tracker" + "_policy",
)


def test_homelab_mcp_has_no_private_tracker_integrations_or_policy_names() -> None:
    offenders: list[str] = []
    for path in MCP_SOURCE.rglob("*"):
        if (
            not path.is_file()
            or any(part.startswith(".") for part in path.relative_to(MCP_SOURCE).parts)
            or "__pycache__" in path.parts
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if FORBIDDEN_IMPORT.search(text) or any(name in text for name in FORBIDDEN_POLICY_NAMES):
            offenders.append(str(path.relative_to(MCP_SOURCE)))
    assert offenders == []
