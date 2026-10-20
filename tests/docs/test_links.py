"""Docs stay navigable: relative links and anchors resolve, and decision records point at code that exists."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)|^\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
CODE_LINE = re.compile(r"^- \*\*Code[^*]*:\*\*(.+?)(?=^- \*\*|^$)", re.MULTILINE | re.DOTALL)


def _markdown_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines()]


def _slug(heading: str) -> str:
    text = re.sub(r"[^\w\- ]", "", heading.replace("`", "").lower())
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    return {_slug(h) for h in HEADING.findall(text)}


def _broken_links(path: Path) -> list[str]:
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    broken = []
    for match in LINK.finditer(text):
        target = match.group(1) or match.group(2)
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        file_part, _, anchor = target.partition("#")
        resolved = (path.parent / file_part).resolve() if file_part else path
        if not resolved.exists():
            broken.append(f"{target}: missing")
        elif anchor and resolved.suffix == ".md" and anchor not in _anchors(resolved):
            broken.append(f"{target}: no heading #{anchor}")
    return broken


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_and_anchors_resolve(path):
    assert _broken_links(path) == []


def test_link_checker_reports_missing_files_and_anchors(tmp_path):
    (tmp_path / "b.md").write_text("# Real heading\n")
    doc = tmp_path / "a.md"
    doc.write_text("[ok](b.md#real-heading) [gone](c.md) [bad](b.md#nope)\n```\n[ignored](x.md)\n```\n")
    assert _broken_links(doc) == ["c.md: missing", "b.md#nope: no heading #nope"]


@pytest.mark.parametrize("path", sorted((ROOT / "docs/decisions").glob("0*.md")), ids=lambda p: p.name)
def test_decision_records_name_code_that_exists(path):
    block = CODE_LINE.search(path.read_text(encoding="utf-8"))
    assert block, "decision record has no **Code:** line"
    paths = [p for p in re.findall(r"`([^`\s]+)`", block.group(1)) if "/" in p or "." in p]
    assert paths, "**Code:** names no paths"
    assert [p for p in paths if not (ROOT / p).exists()] == []
