from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def discover_transcript_files(
    projects_root: Path,
    *,
    now: float,
    window_seconds: int = 86_400,
    name_substr: str = "homelab",
) -> list[Path]:
    cutoff = now - window_seconds
    out: list[Path] = []
    if not projects_root.is_dir():
        return out
    for project in sorted(projects_root.iterdir()):
        if not project.is_dir() or name_substr not in project.name:
            continue
        root = project / "agent-transcripts"
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.jsonl")):
            try:
                if path.stat().st_mtime >= cutoff:
                    out.append(path)
            except OSError:
                continue
    return out


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "\n".join(p for p in parts if p)
    return ""


def load_jsonl_messages(path: Path) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = str(row.get("role") or "")
            message = row.get("message") or {}
            text = _extract_text(message.get("content") if isinstance(message, dict) else "")
            if role and text:
                messages.append({"role": role, "text": text})
    return messages
