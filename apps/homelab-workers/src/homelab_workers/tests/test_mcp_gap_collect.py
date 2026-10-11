import json
import time
from pathlib import Path

from homelab_workers.mcp_gap_closer.collect import (
    discover_transcript_files,
    load_jsonl_messages,
)


def test_discover_only_homelab_projects_within_window(tmp_path: Path) -> None:
    now = time.time()
    keep = tmp_path / "home-jon-homelab" / "agent-transcripts" / "a" / "a.jsonl"
    keep.parent.mkdir(parents=True)
    keep.write_text("{}\n", encoding="utf-8")
    # mtime = now
    skip_old = tmp_path / "home-jon-homelab" / "agent-transcripts" / "b" / "b.jsonl"
    skip_old.parent.mkdir(parents=True)
    skip_old.write_text("{}\n", encoding="utf-8")
    import os

    os.utime(skip_old, (now - 200_000, now - 200_000))
    other = tmp_path / "home-jon-other" / "agent-transcripts" / "c" / "c.jsonl"
    other.parent.mkdir(parents=True)
    other.write_text("{}\n", encoding="utf-8")

    found = discover_transcript_files(tmp_path, now=now, window_seconds=86_400)
    assert found == [keep]


def test_load_jsonl_extracts_text(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    row = {
        "role": "user",
        "message": {"content": [{"type": "text", "text": "hello gap"}]},
    }
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    msgs = load_jsonl_messages(p)
    assert msgs == [{"role": "user", "text": "hello gap"}]
