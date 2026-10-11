from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ci/public-readiness.py"


def run_readiness(repo: Path, marker_file: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if marker_file is not None:
        environment["HOMELAB_PRIVATE_MARKERS_FILE"] = str(marker_file)
    else:
        environment.pop("HOMELAB_PRIVATE_MARKERS_FILE", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=repo,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def make_repo(tmp_path: Path, content: str) -> Path:
    (tmp_path / "sample.txt").write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "sample.txt"], cwd=tmp_path, check=True)
    return tmp_path


def test_generic_rules_and_documentation_examples(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        "private 10.2.3.4\n/home/alice/media\nAbcdef1234\n"
        "safe 127.0.0.1 192.0.2.10 198.51.100.4 203.0.113.9\n",
    )
    result = run_readiness(repo)
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "sample.txt:1:private-network",
        "sample.txt:2:home-path",
        "sample.txt:3:token-shaped",
    ]


def test_allow_list_uses_path_and_rule(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "10.2.3.4\n/home/alice/media\n")
    (repo / "other.txt").write_text("10.2.3.4\n", encoding="utf-8")
    (repo / "scripts/ci").mkdir(parents=True)
    (repo / "scripts/ci/public-readiness-allow.txt").write_text(
        "sample.txt private-network\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "add", "other.txt", "scripts/ci/public-readiness-allow.txt"],
        cwd=repo,
        check=True,
    )
    result = run_readiness(repo)
    assert result.returncode == 1
    assert result.stdout == "other.txt:1:private-network\nsample.txt:2:home-path\n"


def test_svg_geometry_tokens_are_ignored_but_content_is_scanned(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "image.svg").write_text(
        '<svg viewBox="0 0 100 100" aria-label="Abcdef1234"><marker id="my-svg_flowchart-v2-pointEnd" '
        'marker-end="url(#my-svg_flowchart-v2-pointEnd)"/><path d="Abcdef1234" '
        'transform="Abcdef1234"/><text>Abcdef1234</text>'
        '<metadata>Abcdef1234</metadata></svg>\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "image.svg"], cwd=repo, check=True)

    result = run_readiness(repo)

    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "image.svg:1:token-shaped",
        "image.svg:1:token-shaped",
        "image.svg:1:token-shaped",
    ]


def test_marker_file_reports_rule_without_marker_value(tmp_path: Path) -> None:
    secret_marker = "my-private-router-name"
    repo = make_repo(tmp_path, f"visible {secret_marker}\n")
    marker_file = tmp_path / "markers.txt"
    marker_file.write_text(f"# comment\n{secret_marker}\n", encoding="utf-8")
    result = run_readiness(repo, marker_file)
    assert result.returncode == 1
    assert result.stdout == "sample.txt:1:private-marker\n"
    assert secret_marker not in result.stdout
