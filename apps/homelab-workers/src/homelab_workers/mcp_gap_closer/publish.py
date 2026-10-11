from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _run_git(args: list[str], *, cwd: Path, git_bin: str = "git") -> None:
    subprocess.run([git_bin, *args], cwd=cwd, check=True, capture_output=True, text=True)


def publish_inbox_branch(
    *,
    repo_url: str,
    base_ref: str = "dev",
    branch: str = "chore/mcp-gap-inbox",
    pack: dict,
    pack_relpath: str = "docs/superpowers/reports/mcp-gap-inbox-latest.json",
    work_dir: Path,
    git_bin: str = "git",
) -> None:
    clone_root = work_dir / "clone"
    _run_git(["clone", repo_url, str(clone_root)], cwd=work_dir, git_bin=git_bin)
    _run_git(["fetch", "origin", base_ref], cwd=clone_root, git_bin=git_bin)
    _run_git(["checkout", "-B", branch, f"origin/{base_ref}"], cwd=clone_root, git_bin=git_bin)

    pack_path = clone_root / pack_relpath
    pack_path.parent.mkdir(parents=True, exist_ok=True)
    pack_path.write_text(
        json.dumps(pack, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    _run_git(["add", pack_relpath], cwd=clone_root, git_bin=git_bin)
    _run_git(
        ["commit", "-m", "chore: update mcp-gap inbox pack"],
        cwd=clone_root,
        git_bin=git_bin,
    )
    _run_git(
        ["push", "--force-with-lease", "origin", branch],
        cwd=clone_root,
        git_bin=git_bin,
    )
