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


def run(dest: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DESTDIR": str(dest), "HOMELAB_PIP": "true", **(env or {})}
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


def test_homelabctl_finds_its_own_homelab_backup_first(tmp_path: Path):
    dest = tmp_path / "dest"
    assert run(dest).returncode == 0
    tooling = dest / "srv/homelab/tooling" / SHA
    python = tooling / "venv/bin/python"
    python.unlink()
    python.write_text("#!/bin/sh\ncommand -v homelab-backup\n")
    python.chmod(0o755)
    stale = tmp_path / "sbin"
    stale.mkdir()
    (stale / "homelab-backup").write_text("#!/bin/sh\n")
    (stale / "homelab-backup").chmod(0o755)
    env = {**os.environ, "PATH": f"{stale}:{os.environ['PATH']}"}
    found = subprocess.run([str(tooling / "bin/homelabctl")], env=env, text=True, capture_output=True, check=True)
    assert Path(found.stdout.strip()).resolve() == (tooling / "host/homelab-backup").resolve()


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
    backups = list((tmp_path / "srv/homelab/state/install-backup").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "etc/systemd/system/homelab-backup.service").read_text() == "changed\n"


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


def test_replaces_previous_target_after_upgrade(tmp_path: Path):
    assert run(tmp_path).returncode == 0
    tooling = tmp_path / "srv/homelab/tooling"
    (tooling / "A").mkdir()
    (tooling / "B").mkdir()
    (tooling / "current").unlink()
    (tooling / "current").symlink_to("B")
    (tooling / "previous").symlink_to("A")
    assert run(tmp_path).returncode == 0
    assert not (tooling / "A").exists() and (tooling / "B").is_dir()
    assert os.readlink(tooling / "previous") == "B"


def test_host_files_dropin_and_no_infisical_unit(tmp_path: Path):
    assert run(tmp_path).returncode == 0
    assert (tmp_path / "etc/systemd/system/docker.service.d/10-homelab.conf").read_text() == (
        "[Unit]\nAfter=tailscaled.service homelab-infisical-agent.service\n"
    )
    assert (tmp_path / "srv/homelab/HOST-FILES").read_text() == (
        "/etc/systemd/system/docker.service.d/10-homelab.conf\n"
        "/etc/systemd/system/homelab-backup.service\n"
        "/etc/systemd/system/homelab-backup.timer\n"
        "/etc/systemd/system/homelab-controller.service\n"
        "/etc/systemd/system/homelab-controller.timer\n"
        "/etc/systemd/system/homelab-infisical-agent.service.d/20-secrets.conf\n"
        "/etc/sysctl.d/90-homelab.conf\n/var/lib/tailscale\n"
    )
    # Install owns only the agent's tmpfs drop-in; the agent unit itself is installed by hand (docs/operations.md).
    assert not list((tmp_path / "etc").rglob("homelab-infisical-agent.service"))
    agent = (tmp_path / "etc/systemd/system/homelab-infisical-agent.service.d/20-secrets.conf").read_text()
    assert "RuntimeDirectory=homelab-secrets\nRuntimeDirectoryMode=0700\nRuntimeDirectoryPreserve=yes\n" in agent
    assert "ExecStartPost=/srv/homelab/tooling/current/host/wait-for-secrets\n" in agent


def test_keeps_host_files_lines_added_by_other_tools(tmp_path: Path):
    assert run(tmp_path).returncode == 0
    host_files = tmp_path / "srv/homelab/HOST-FILES"
    dropin = "/etc/systemd/system/homelab-infisical-agent.service.d/10-homelab.conf\n"
    host_files.write_text(host_files.read_text() + dropin)
    again = run(tmp_path)
    assert again.returncode == 0 and "no changes" in again.stdout
    assert host_files.read_text().endswith(dropin)

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


def test_pip_failure_removes_new_tooling_without_units_or_marker(tmp_path: Path):
    result = run(tmp_path, env={"HOMELAB_PIP": "false"})
    assert result.returncode != 0
    assert not list((tmp_path / "etc").rglob("*.service"))
    assert not (tmp_path / "srv/homelab/tooling/current").exists()
    assert not (tmp_path / "srv/homelab/tooling" / SHA).exists()
    assert not (tmp_path / "srv/homelab/state/observe-only").exists()


def test_file_install_failure_restores_old_current_and_prior_files(tmp_path: Path):
    tooling = tmp_path / "srv/homelab/tooling"
    (tooling / "old").mkdir(parents=True)
    (tooling / "current").symlink_to("old")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_install = fake_bin / "install"
    fake_install.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in *homelab-backup.timer) exit 7;; esac\n"
        "exec /usr/bin/install \"$@\"\n"
    )
    fake_install.chmod(0o755)
    result = run(tmp_path, env={"PATH": f"{fake_bin}:{os.environ['PATH']}"})
    assert result.returncode != 0
    assert os.readlink(tooling / "current") == "old"
    assert not (tooling / SHA).exists()
    for name in (
        "homelab-controller.service", "homelab-controller.timer",
        "homelab-backup.service", "homelab-backup.timer",
    ):
        assert not (tmp_path / "etc/systemd/system" / name).exists()


def test_touch_failure_removes_new_observe_only_marker(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_touch = fake_bin / "touch"
    fake_touch.write_text(
        "#!/bin/sh\n"
        "/usr/bin/touch \"$@\"\n"
        "case \"$*\" in *observe-only) exit 7;; esac\n"
    )
    fake_touch.chmod(0o755)
    result = run(tmp_path, env={"PATH": f"{fake_bin}:{os.environ['PATH']}"})
    assert result.returncode == 7
    assert not (tmp_path / "srv/homelab/state/observe-only").exists()
    assert not (tmp_path / "srv/homelab/tooling/current").exists()
    assert not (tmp_path / "srv/homelab/tooling" / SHA).exists()
