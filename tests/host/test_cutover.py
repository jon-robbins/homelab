"""Contract tests for the host cutover command."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "host/cutover.sh"


def setup_host(tmp_path: Path, *, container: bool = True, mismatch: bool = False,
               dns_mismatch: bool = False, inactive: bool = False,
               fail_agent_restart: bool = False, fail_chattr: bool = False,
               fail_systemd_run: bool = False, empty_ip: bool = False,
               fail_status: bool = False, fail_timer_stop: bool = False):
    root = tmp_path / "root"
    legacy = root / "legacy"
    (legacy / "config/infisical/templates").mkdir(parents=True)
    (legacy / "config/infisical/templates/KEY.tmpl").write_text("key")
    (root / "srv/homelab/tooling/current/venv/bin").mkdir(parents=True)
    (root / "srv/homelab/tooling/current/venv/bin/python").symlink_to(sys.executable)
    (root / "etc/infisical-agent").mkdir(parents=True)
    (root / "etc/infisical-agent/config.yaml").write_text(
        "items:\n  - source-path: %s\n    commands: [bad]\n"
        % (legacy / "config/infisical/templates/KEY.tmpl")
    )
    media = root / "media"
    media.mkdir(parents=True)
    (root / "unmounted").mkdir()
    (root / "tmp").mkdir()
    source = root / "tailscale-source"
    source.mkdir()
    (source / "tailscaled.state").write_text("state")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "commands.log"
    fake = bindir / "fake"
    fake.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
n=pathlib.Path(sys.argv[0]).name; a=sys.argv[1:]
pathlib.Path(os.environ['LOG']).open('a').write(n+' '+' '.join(a)+'\\n')
if n == 'docker':
 if a[:2] == ['ps','-q']: print('c' if os.environ.get('CONTAINER') else '')
 elif a[:1] == ['exec']: print(json.dumps({'Self':{'HostName':'old','TailscaleIPs':['1.2.3.4'],'DNSName':'old.ts.net'}}))
 elif a[:1] == ['inspect'] and a[-1] == 'c': print(os.environ['SOURCE'])
 elif a[:1] == ['inspect']: print('unless-stopped')
elif n == 'tailscale' and a[:2] == ['status','--json']:
 if os.environ.get('FAIL_STATUS'): sys.exit(1)
 print(json.dumps({'BackendState':'Running','Self':{'TailscaleIPs':[''] if os.environ.get('EMPTY_IP') else ['9.9.9.9'] if os.environ.get('MISMATCH') else ['1.2.3.4'],'DNSName':'new.ts.net' if os.environ.get('MISMATCH') or os.environ.get('DNS_MISMATCH') else 'old.ts.net'}}))
elif n == 'systemctl':
 if a[:2] == ['is-active','homelab-infisical-agent'] and os.environ.get('INACTIVE'): sys.exit(1)
 if a[:2] == ['restart','homelab-infisical-agent'] and os.environ.get('FAIL_AGENT_RESTART'): sys.exit(1)
 if a[:2] == ['stop','homelab-cutover-deadman.timer'] and os.environ.get('FAIL_TIMER_STOP'): sys.exit(1)
elif n == 'chattr' and os.environ.get('FAIL_CHATTR'): sys.exit(1)
elif n == 'systemd-run' and os.environ.get('FAIL_SYSTEMD_RUN'): sys.exit(1)
elif n == 'mountpoint': sys.exit(1 if a[-1].endswith('/unmounted') else 0)
elif n == 'cp': sys.exit(subprocess.call(['/bin/cp', *a]))
"""
    )
    fake.chmod(0o755)
    for name in "docker systemctl systemd-run tailscale mountpoint mount umount chattr cp".split():
        (bindir / name).symlink_to(fake)
    env = os.environ | {"ROOT": str(root), "LOG": str(log), "SOURCE": "/tailscale-source",
                        "CONTAINER": "1" if container else "", "MISMATCH": "1" if mismatch else "",
                        "DNS_MISMATCH": "1" if dns_mismatch else "", "INACTIVE": "1" if inactive else "",
                        "FAIL_AGENT_RESTART": "1" if fail_agent_restart else "",
                        "FAIL_CHATTR": "1" if fail_chattr else "",
                        "FAIL_SYSTEMD_RUN": "1" if fail_systemd_run else "",
                        "EMPTY_IP": "1" if empty_ip else "",
                        "FAIL_STATUS": "1" if fail_status else "",
                        "FAIL_TIMER_STOP": "1" if fail_timer_stop else ""}
    return root, legacy, media, env, log


def run_cutover(tmp_path: Path, *args: str, **kw):
    root, legacy, media, env, log = setup_host(tmp_path, **kw)
    cmd = [str(SCRIPT), "--legacy-root", "/legacy", "--media-root", "/media", *args]
    env["PATH"] = str(tmp_path / "bin") + ":" + os.environ["PATH"]
    return subprocess.run(cmd, env=env,
                          text=True, capture_output=True), root, legacy, log


def test_cutover_rewrites_config_and_rollback(tmp_path: Path):
    root, legacy, media, env, log = setup_host(tmp_path)
    env["PATH"] = str(tmp_path / "bin") + ":" + os.environ["PATH"]
    base = [str(SCRIPT), "--legacy-root", "/legacy", "--media-root", "/media",
            "--media-root", "/unmounted"]
    result = subprocess.run(base, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    config = (root / "etc/infisical-agent/config.yaml").read_text()
    assert "commands" not in config
    assert "/srv/homelab/infisical/templates/KEY.tmpl" in config
    assert (root / "media/.homelab-mounted").exists()
    assert not (root / "unmounted/.homelab-mounted").exists()
    assert "skip: /unmounted is not a mountpoint" in result.stdout
    forward = log.read_text().splitlines()
    def at(command: str) -> int:
        return next(i for i, line in enumerate(forward) if line.startswith(command))
    assert at("docker stop c") < at("systemctl unmask tailscaled")
    assert at("systemctl enable --now tailscaled") < at("docker rm c")
    assert at("docker rm c") < at("systemctl disable --now homelab-boot-orchestrator.service")
    assert at("systemctl restart homelab-infisical-agent") < at("mountpoint -q")
    assert at("chattr +i") < at("docker inspect -f {{.HostConfig.RestartPolicy.Name}} 3x-ui")
    assert at("systemd-run --unit=homelab-cutover-deadman") < at("docker stop c")
    assert "--legacy-root /legacy --media-root /media --media-root /unmounted --rollback" in next(
        line for line in forward if line.startswith("systemd-run "))
    assert "3x-ui restart policy: unless-stopped" in result.stdout
    assert "cutover done; automatic rollback in 10 min unless confirmed." in result.stdout
    assert f"From a new SSH session over Tailscale run: {SCRIPT.resolve()} --confirm" in result.stdout
    assert str(root / "etc/systemd/system/homelab-infisical-agent.service.d/10-homelab.conf") in (
        root / "srv/homelab/HOST-FILES").read_text()
    mounts = [line.split() for line in forward if line.startswith("mount --bind ")]
    assert len(mounts) == 1
    assert mounts[0][2] == f"{root}/"
    assert mounts[0][3].startswith(f"{root}/tmp/homelab-cutover.")
    assert any(line.startswith(f"chattr +i {root}/tmp/homelab-cutover.") and
               line.endswith("/media") for line in forward)
    assert not any(line.startswith("chattr ") and line.endswith("/unmounted")
                   for line in forward)
    result = subprocess.run(base + ["--rollback"], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert not (root / "media/.homelab-mounted").exists()
    assert "commands: [bad]" in (root / "etc/infisical-agent/config.yaml").read_text()
    rollback = log.read_text().splitlines()[len(forward):]
    assert any(line.startswith("chattr -i ") and line.endswith("/media")
               for line in rollback)
    assert "systemctl enable homelab-boot-orchestrator.service" in rollback
    assert "systemctl disable --now tailscaled" in rollback
    assert "systemctl mask tailscaled" in rollback
    assert f"cp -a {root}/var/lib/tailscale/. {root}/tailscale-source/" in rollback
    assert f"docker compose -p homelab -f {legacy}/docker-compose.yml up -d --no-deps tailscale" in rollback
    assert not any(line.startswith("chattr ") and line.endswith("/unmounted")
                   for line in rollback)


def test_dry_run_does_not_mutate(tmp_path: Path):
    result, root, _, log = run_cutover(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert not (root / "media/.homelab-mounted").exists()
    assert "docker stop" not in log.read_text()
    assert f"mktemp -d {root}/tmp/homelab-cutover.XXXXXX" in result.stdout
    assert f"mount --bind {root}/ {root}/tmp/homelab-cutover.XXXXXX" in result.stdout
    assert "tailscale.json" in result.stdout and r"docker\ exec" in result.stdout
    assert "tailscale-source" in result.stdout and "printf" in result.stdout
    assert "[dry-run] systemd-run --unit=homelab-cutover-deadman" in result.stdout
    assert "systemd-run " not in log.read_text()
    assert not (root / "srv/homelab/state/cutover/tailscale.json").exists()
    assert not (root / "srv/homelab/state/cutover/tailscale-source").exists()


def test_identity_mismatch_keeps_container(tmp_path: Path):
    result, _, _, log = run_cutover(tmp_path, mismatch=True)
    assert result.returncode == 1
    logged = log.read_text()
    assert "docker rm" not in logged
    assert "systemctl enable homelab-boot-orchestrator.service" in logged
    assert "systemctl mask tailscaled" in logged
    assert "docker compose -p homelab" in logged


def test_dns_mismatch_keeps_container(tmp_path: Path):
    result, _, _, log = run_cutover(tmp_path, dns_mismatch=True)
    assert result.returncode == 1
    assert "docker rm" not in log.read_text()


def test_missing_tailscale_is_skipped(tmp_path: Path):
    result, _, _, _ = run_cutover(tmp_path, container=False)
    assert result.returncode == 0, result.stderr
    assert "skipping" in result.stdout


def test_deadman_failure_stops_before_mutation(tmp_path: Path):
    result, _, _, log = run_cutover(tmp_path, fail_systemd_run=True)
    assert result.returncode != 0
    assert log.read_text().splitlines() == [
        "systemd-run --unit=homelab-cutover-deadman --on-active=10min "
        f"{SCRIPT.resolve()} --legacy-root /legacy --media-root /media --rollback"
    ]


def test_confirm_stops_deadman_when_identity_and_agent_are_ok(tmp_path: Path):
    root, _, _, env, log = setup_host(tmp_path)
    state = root / "srv/homelab/state/cutover"
    state.mkdir(parents=True)
    (state / "tailscale.json").write_text(json.dumps({
        "Self": {"TailscaleIPs": ["1.2.3.4"], "DNSName": "old.ts.net"}
    }))
    env["PATH"] = str(tmp_path / "bin") + ":" + os.environ["PATH"]
    result = subprocess.run([str(SCRIPT), "--confirm"], env=env, text=True,
                            capture_output=True)
    assert result.returncode == 0 and result.stdout.strip() == "confirmed"
    assert "systemctl stop homelab-cutover-deadman.timer" in log.read_text()


def test_confirm_identity_mismatch_does_not_stop_deadman(tmp_path: Path):
    root, _, _, env, log = setup_host(tmp_path, mismatch=True)
    state = root / "srv/homelab/state/cutover"
    state.mkdir(parents=True)
    (state / "tailscale.json").write_text(json.dumps({
        "Self": {"TailscaleIPs": ["1.2.3.4"], "DNSName": "old.ts.net"}
    }))
    env["PATH"] = str(tmp_path / "bin") + ":" + os.environ["PATH"]
    result = subprocess.run([str(SCRIPT), "--confirm"], env=env, text=True,
                            capture_output=True)
    assert result.returncode == 1
    assert "systemctl stop homelab-cutover-deadman.timer" not in log.read_text()


def test_confirm_inactive_agent_does_not_stop_deadman(tmp_path: Path):
    root, _, _, env, log = setup_host(tmp_path, inactive=True)
    state = root / "srv/homelab/state/cutover"
    state.mkdir(parents=True)
    (state / "tailscale.json").write_text(json.dumps({
        "Self": {"TailscaleIPs": ["1.2.3.4"], "DNSName": "old.ts.net"}
    }))
    env["PATH"] = str(tmp_path / "bin") + ":" + os.environ["PATH"]
    result = subprocess.run([str(SCRIPT), "--confirm"], env=env, text=True,
                            capture_output=True)
    assert result.returncode == 1
    assert "systemctl stop homelab-cutover-deadman.timer" not in log.read_text()


def test_confirm_rejects_empty_first_ip(tmp_path: Path):
    root, _, _, env, log = setup_host(tmp_path, empty_ip=True)
    state = root / "srv/homelab/state/cutover"
    state.mkdir(parents=True)
    (state / "tailscale.json").write_text(json.dumps({
        "Self": {"TailscaleIPs": [""], "DNSName": "old.ts.net"}
    }))
    env["PATH"] = str(tmp_path / "bin") + ":" + os.environ["PATH"]
    result = subprocess.run([str(SCRIPT), "--confirm"], env=env, text=True,
                            capture_output=True)
    assert result.returncode == 1
    assert "tailscale identity check failed" in result.stdout
    assert "systemctl stop homelab-cutover-deadman.timer" not in log.read_text()


def test_confirm_reports_status_failure(tmp_path: Path):
    root, _, _, env, log = setup_host(tmp_path, fail_status=True)
    state = root / "srv/homelab/state/cutover"
    state.mkdir(parents=True)
    (state / "tailscale.json").write_text(json.dumps({
        "Self": {"TailscaleIPs": ["1.2.3.4"], "DNSName": "old.ts.net"}
    }))
    env["PATH"] = str(tmp_path / "bin") + ":" + os.environ["PATH"]
    result = subprocess.run([str(SCRIPT), "--confirm"], env=env, text=True,
                            capture_output=True)
    assert result.returncode == 1
    assert "tailscale status failed" in result.stdout
    assert "systemctl stop homelab-cutover-deadman.timer" not in log.read_text()


def test_confirm_reports_timer_stop_failure(tmp_path: Path):
    root, _, _, env, log = setup_host(tmp_path, fail_timer_stop=True)
    state = root / "srv/homelab/state/cutover"
    state.mkdir(parents=True)
    (state / "tailscale.json").write_text(json.dumps({
        "Self": {"TailscaleIPs": ["1.2.3.4"], "DNSName": "old.ts.net"}
    }))
    env["PATH"] = str(tmp_path / "bin") + ":" + os.environ["PATH"]
    result = subprocess.run([str(SCRIPT), "--confirm"], env=env, text=True,
                            capture_output=True)
    assert result.returncode == 1
    assert "failed to stop homelab-cutover-deadman.timer" in result.stdout
    assert "confirmed" not in result.stdout
    assert "systemctl stop homelab-cutover-deadman.timer" in log.read_text()


def test_agent_restart_failure_runs_rollback(tmp_path: Path):
    result, _, _, log = run_cutover(tmp_path, fail_agent_restart=True)
    assert result.returncode != 0
    logged = log.read_text()
    assert "systemctl enable homelab-boot-orchestrator.service" in logged
    assert "systemctl mask tailscaled" in logged
    assert "docker compose -p homelab" in logged


def test_rollback_continues_after_media_failure(tmp_path: Path):
    root, legacy, _, env, log = setup_host(tmp_path)
    env["PATH"] = str(tmp_path / "bin") + ":" + os.environ["PATH"]
    base = [str(SCRIPT), "--legacy-root", "/legacy", "--media-root", "/media"]
    assert subprocess.run(base, env=env, text=True, capture_output=True).returncode == 0
    env["FAIL_CHATTR"] = "1"
    result = subprocess.run(base + ["--rollback"], env=env, text=True, capture_output=True)
    assert result.returncode == 1
    logged = log.read_text()
    assert "chattr -i" in logged
    assert "systemctl enable homelab-boot-orchestrator.service" in logged
    assert "docker compose -p homelab" in logged
