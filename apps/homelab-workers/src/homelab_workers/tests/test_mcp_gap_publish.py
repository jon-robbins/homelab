from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from homelab_workers.cli import mcp_gap_inbox_sync
from homelab_workers.mcp_gap_closer.publish import publish_inbox_branch


def _mock_git(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> object:
        calls.append(list(cmd))
        if len(cmd) >= 3 and cmd[1] == "clone":
            clone_root = Path(cmd[-1])
            clone_root.mkdir(parents=True, exist_ok=True)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("homelab_workers.mcp_gap_closer.publish.subprocess.run", fake_run)
    return calls


def test_publish_inbox_branch_git_sequence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_git(monkeypatch)
    pack = {
        "generated_at": "2026-08-10T06:00:00Z",
        "window_hours": 24,
        "chats": [{"id": "c1", "project": "home-jon-homelab", "excerpts": ["docker compose exec"]}],
    }

    publish_inbox_branch(
        repo_url="https://github.com/example/homelab.git",
        base_ref="dev",
        branch="chore/mcp-gap-inbox",
        pack=pack,
        pack_relpath="docs/superpowers/reports/mcp-gap-inbox-latest.json",
        work_dir=tmp_path,
        git_bin="git",
    )

    flat = " ".join(" ".join(c) for c in calls)
    assert "clone" in flat
    assert "fetch" in flat
    assert "checkout" in flat or "switch" in flat
    assert "add" in flat
    assert "commit" in flat
    assert "push" in flat and "force-with-lease" in flat

    clone_root = tmp_path / "clone"
    pack_path = clone_root / "docs/superpowers/reports/mcp-gap-inbox-latest.json"
    assert pack_path.is_file()
    written = json.loads(pack_path.read_text(encoding="utf-8"))
    assert written["chats"][0]["id"] == "c1"


def test_cli_empty_pack_skips_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("APP_DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        mcp_gap_inbox_sync,
        "discover_transcript_files",
        lambda *_a, **_k: [],
    )
    published: list[dict] = []
    monkeypatch.setattr(
        mcp_gap_inbox_sync,
        "publish_inbox_branch",
        lambda **kwargs: published.append(kwargs),
    )

    rc = mcp_gap_inbox_sync.main(
        [
            "--projects-root",
            str(tmp_path),
            "--no-publish",
        ]
    )

    assert rc == 0
    assert published == []
    pack_path = data_dir / "mcp-gap-inbox" / "latest.json"
    assert pack_path.is_file()
    assert pack_path.is_relative_to(tmp_path)
    assert json.loads(pack_path.read_text(encoding="utf-8"))["chats"] == []


def test_cli_nonempty_pack_publishes_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript = tmp_path / "home-jon-homelab" / "agent-transcripts" / "t.jsonl"
    transcript.parent.mkdir(parents=True)
    row = {
        "role": "assistant",
        "message": {"content": [{"type": "text", "text": "used docker compose exec sonarr"}]},
    }
    transcript.write_text(json.dumps(row) + "\n", encoding="utf-8")

    published: list[dict] = []
    monkeypatch.setattr(
        mcp_gap_inbox_sync,
        "publish_inbox_branch",
        lambda **kwargs: published.append(kwargs),
    )
    monkeypatch.setattr(mcp_gap_inbox_sync, "_default_repo_url", lambda: "https://example/homelab.git")
    monkeypatch.setattr(mcp_gap_inbox_sync, "_local_pack_path", lambda: tmp_path / "latest.json")

    rc = mcp_gap_inbox_sync.main(
        [
            "--projects-root",
            str(tmp_path),
            "--repo-url",
            "https://example/homelab.git",
        ]
    )

    assert rc == 0
    assert len(published) == 1
    assert published[0]["repo_url"] == "https://example/homelab.git"
    assert published[0]["pack"]["chats"]
