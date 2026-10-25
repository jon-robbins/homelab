from __future__ import annotations

import os
from pathlib import Path
import subprocess

SCRIPT = Path(__file__).parents[2] / "config/qbittorrent/dispatcher.sh"
HASH = "abc123"


def run(tmp_path: Path, url: str | None) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "wget.log"
    stub = bin_dir / "wget"
    stub.write_text(
        '#!/bin/sh\necho "$@" >> "$WGET_LOG"\n'
        'echo "  HTTP/1.1 200 OK" >&2\n'
        'out=""; prev=""\nfor a in "$@"; do [ "$prev" = "-O" ] && out="$a"; prev="$a"; done\n'
        '[ -n "$out" ] && echo \'{"handled":true}\' > "$out"\nexit 0\n'
    )
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "QBT_DISPATCH_EVENTS_URL"}
    env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", WGET_LOG=str(log))
    if url is not None:
        env["QBT_DISPATCH_EVENTS_URL"] = url
    result = subprocess.run(["sh", str(SCRIPT), HASH], env=env, text=True, capture_output=True, timeout=10)
    return result, log


def test_unset_url_exits_zero_without_posting(tmp_path: Path):
    result, log = run(tmp_path, None)
    assert result.returncode == 0
    assert f"no QBT_DISPATCH_EVENTS_URL set; nothing to notify hash={HASH}" in result.stdout
    assert not log.exists()


def test_empty_url_exits_zero_without_posting(tmp_path: Path):
    result, log = run(tmp_path, "")
    assert result.returncode == 0 and not log.exists()


def test_set_url_posts_the_hash(tmp_path: Path):
    result, log = run(tmp_path, "http://events.example/v1/torrent-finished")
    assert result.returncode == 0, result.stdout
    calls = log.read_text()
    assert "http://events.example/v1/torrent-finished" in calls and f'{{"hash":"{HASH}"}}' in calls
