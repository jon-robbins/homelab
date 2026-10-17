from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[2]
INSTALL = ROOT / "host/install.sh"
SHA = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def run(dest: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DESTDIR": str(dest), "HOMELAB_PIP": "true"}
    return subprocess.run([str(INSTALL), "--sha", SHA, *args], cwd=ROOT, env=env,
                          text=True, capture_output=True)


def tree(root: Path) -> dict[str, bytes | str]:
    result = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            result[rel] = os.readlink(path)
        elif path.is_file():
            result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def test_install_is_idempotent_and_only_initializes_observe_only(tmp_path: Path):
    assert run(tmp_path).returncode == 0
    before = tree(tmp_path)
    second = run(tmp_path)
    assert second.returncode == 0 and "no changes" in second.stdout
    assert tree(tmp_path) == before
    (tmp_path / "srv/homelab/state/observe-only").unlink()
    assert run(tmp_path).returncode == 0
    assert not (tmp_path / "srv/homelab/state/observe-only").exists()


def test_conflict_requires_replace(tmp_path: Path):
    assert run(tmp_path).returncode == 0
    unit = tmp_path / "etc/systemd/system/homelab-backup.service"
    unit.write_text("changed\n")
    before = tree(tmp_path)
    refused = run(tmp_path)
    assert refused.returncode == 1 and "---" in refused.stdout
    assert tree(tmp_path) == before
    replaced = run(tmp_path, "--replace", "homelab-backup")
    assert replaced.returncode == 0 and "Type=oneshot" in unit.read_text()


def test_removes_obsolete_tooling_tree(tmp_path: Path):
    assert run(tmp_path).returncode == 0
    obsolete = tmp_path / "srv/homelab/tooling/obsolete"
    (obsolete / "nested").mkdir(parents=True)
    (obsolete / "nested/file").write_text("old\n")
    assert run(tmp_path).returncode == 0
    assert not obsolete.exists()


def test_preserves_previous_tooling_tree(tmp_path: Path):
    assert run(tmp_path).returncode == 0
    tooling = tmp_path / "srv/homelab/tooling"
    (tooling / "old").mkdir()
    (tooling / "previous").symlink_to("old")
    assert run(tmp_path).returncode == 0
    assert (tooling / "old").is_dir()
    assert os.readlink(tooling / "previous") == "old"


def test_host_files_dropin_and_no_infisical_unit(tmp_path: Path):
    assert run(tmp_path).returncode == 0
    assert (tmp_path / "etc/systemd/system/docker.service.d/10-homelab.conf").read_text() == "[Unit]\nAfter=tailscaled.service\n"
    assert (tmp_path / "srv/homelab/HOST-FILES").read_text() == (
        "/etc/systemd/system/docker.service.d/10-homelab.conf\n"
        "/etc/systemd/system/homelab-backup.service\n"
        "/etc/systemd/system/homelab-backup.timer\n"
        "/etc/systemd/system/homelab-controller.service\n"
        "/etc/systemd/system/homelab-controller.timer\n"
        "/etc/sysctl.d/90-homelab.conf\n/var/lib/tailscale\n"
    )
    assert not list((tmp_path / "etc").rglob("homelab-infisical-agent.*"))


def test_units_verify_with_systemd_analyze(tmp_path: Path):
    systemd_analyze = shutil.which("systemd-analyze")
    if systemd_analyze is None:
        pytest.skip("systemd-analyze is unavailable")
    assert run(tmp_path).returncode == 0
    (tmp_path / "etc/systemd/system/sysinit.target").write_text("[Unit]\n")
    units = [
        "/etc/systemd/system/" + name
        for name in (
            "homelab-controller.service",
            "homelab-controller.timer",
            "homelab-backup.service",
            "homelab-backup.timer",
        )
    ]
    subprocess.run([systemd_analyze, "--root", str(tmp_path), "verify", *units], check=True)


def test_sha_mismatch_writes_nothing(tmp_path: Path):
    result = subprocess.run([str(INSTALL), "--sha", "0" * 40], cwd=ROOT,
                            env={**os.environ, "DESTDIR": str(tmp_path)},
                            text=True, capture_output=True)
    assert result.returncode == 1
    assert not list(tmp_path.iterdir())
