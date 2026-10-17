"""Apply verified release candidates to a host."""

from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import tempfile
import time
from typing import Any, Protocol
import urllib.request

from scripts.controller.admission import verify_record
from scripts.controller.feed import fetch_newest
from scripts.controller.materialize import materialize
from scripts.controller.planner import ContainerView, DockerAdapter, compose_commands, plan
from scripts.controller.state import State


class Ops(DockerAdapter, Protocol):
    def reachable(self) -> bool: ...
    def free_pct(self, path: str) -> float: ...
    def pull(self, release_dir: Path, services: tuple[str, ...]) -> None: ...
    def stop(self, service: str) -> None: ...
    def start(self, service: str) -> None: ...
    def run(self, argv: list[str]) -> None: ...
    def healthy(self, service: str, timeout_s: int) -> bool: ...
    def probe(self, service: str) -> bool: ...
    def snapshot(self, service: str, tag: str) -> str: ...
    def restore(self, service: str, tag: str) -> None: ...


class Pinger(Protocol):
    def ok(self) -> None: ...
    def fail(self, reason: str) -> None: ...


def _link(path: Path, target: Path | None) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    if target is not None:
        temporary.symlink_to(target.resolve())
        os.replace(temporary, path)
    else:
        path.unlink(missing_ok=True)


def _managed(release: Path) -> dict[str, dict[str, Any]]:
    app = json.loads((release / "app.json").read_bytes())
    return app.get("services", {})


def _degraded(state: State, sequence: int | None, service: str, snapshot: str | None,
              reason: str, pinger: Pinger) -> str:
    text = f"service: {service}\nsnapshot: {snapshot or 'none'}\nreason: {reason}\nnext: homelabctl rollback {service}"
    if sequence is not None: state.set_highwater(sequence)
    state.write("DEGRADED", text + "\n")
    state.remove("applying")
    pinger.fail(f"service: {service}\nnext: homelabctl rollback {service}")
    return text


def _preapply(state: State, cause: str, pinger: Pinger, causes: list[str]) -> str:
    count = int(state.read("failures", "0").strip()) + 1
    state.write("failures", f"{count}\n")
    message = f"pre-apply failed {count}×: {cause}"
    if count >= 3 or causes: pinger.fail(message if count >= 3 else "; ".join(causes))
    else: pinger.ok()
    return message


def _recorded_compose_version(state: State) -> str | None:
    value = state.read("compose-version")
    return value.strip() if value is not None else None


def reconcile(state: State, feed_policy: Any, releases: Path, ops: Ops,
              pinger: Pinger, *, now: bool) -> str:
    releases = Path(releases)
    with state.lock():
        applying = state.read("applying")
        if applying is not None:
            reason = f"interrupted apply of seq {applying.strip()}"
            state.set_highwater(int(applying.strip()))
            state.write("DEGRADED", reason + "\n")
            pinger.fail(reason)
            return reason
        degraded = state.read("DEGRADED")
        if degraded is not None:
            pinger.fail(degraded.strip()); return degraded.strip()
        if not ops.reachable():
            pinger.fail("docker unreachable"); return "docker unreachable"

        causes = []
        if ops.free_pct("/") < 10: causes.append("low free space on /")
        if ops.free_pct("/mnt/media-hdd") < 10: causes.append("low free space on /mnt/media-hdd")
        current = releases / "current"
        if current.is_symlink() or current.is_dir():
            causes.extend(plan(current, ops, _recorded_compose_version(state)).failures)
        try:
            candidate = fetch_newest(state.highwater(), feed_policy)
        except Exception as error: return _degraded(state, None, "unknown", None, str(error), pinger)
        if candidate is None:
            (pinger.fail if causes else pinger.ok)("; ".join(causes)) if causes else pinger.ok()
            return "ok"
        try:
            policy = getattr(feed_policy, "host_policy", None)
            if policy is None and hasattr(feed_policy, "trusted_root"):
                policy = feed_policy
            if policy is not None:
                with tempfile.TemporaryDirectory(dir=policy.feed_directory) as directory:
                    root = Path(directory)
                    (root / "record.json").write_bytes(candidate.record_bytes)
                    (root / "record.sigstore.json").write_bytes(candidate.signature_bytes)
                    (root / "bundle.tar").write_bytes(candidate.bundle_bytes)
                    verify_record(root, policy)
        except Exception as error: return _preapply(state, str(error), pinger, causes)
        try:
            release = materialize(candidate, releases)
        except Exception as error: return _preapply(state, str(error), pinger, causes)
        p = plan(release, ops, _recorded_compose_version(state))
        if state.read("observe-only") is not None and not now:
            (pinger.fail if causes else pinger.ok)("; ".join(causes)) if causes else pinger.ok()
            return "ok"
        if p.hold:
            pinger.fail(p.hold); return p.hold
        if not p.recreate and not p.create_stopped:
            _link(releases / "previous", releases / "current" if current.exists() else None)
            _link(releases / "current", releases / str(candidate.sequence))
            state.set_highwater(candidate.sequence)
            state.write("failures", "0\n")
            (pinger.fail if causes else pinger.ok)("; ".join(causes)) if causes else pinger.ok()
            return "ok"
        if ops.free_pct("/") < 10: return _preapply(state, "low free space on /", pinger, causes)
        services = tuple(sorted(set(p.recreate) | set(p.create_stopped)))
        snapshots: dict[str, str] = {}
        labels = _managed(release)
        try:
            ops.pull(release, services)
            for service in p.recreate:
                if labels.get(service, {}).get("labels", {}).get("homelab.state") == "stateful":
                    ops.stop(service)
                    try:
                        snapshots[service] = ops.snapshot(service, f"seq-{candidate.sequence}")
                    except Exception as error:
                        ops.start(service)
                        return _preapply(state, str(error), pinger, causes)
        except Exception as error: return _preapply(state, str(error), pinger, causes)
        service = next(iter(p.recreate), next(iter(p.create_stopped), "unknown"))
        try:
            state.write("applying", f"{candidate.sequence}\n")
            for argv in compose_commands(p, release):
                ops.run(argv)
            for service in p.recreate:
                if not ops.healthy(service, 180): raise RuntimeError(f"{service} failed health check")
                if labels.get(service, {}).get("labels", {}).get("homelab.probe") and not ops.probe(service): raise RuntimeError(f"{service} failed probe")
        except Exception as error:
            snapshot = snapshots.get(service)
            return _degraded(state, candidate.sequence, service, snapshot, str(error), pinger)
        _link(releases / "previous", releases / "current" if current.exists() else None)
        _link(releases / "current", releases / str(candidate.sequence))
        state.set_highwater(candidate.sequence)
        state.write("compose-version", ops.compose_version() + "\n")
        state.write("failures", "0\n")
        state.remove("applying")
        (pinger.fail if causes else pinger.ok)("; ".join(causes)) if causes else pinger.ok()
        return "ok"


class SubprocessOps:
    """Small production adapter; deployments may replace it with a richer one."""
    def reachable(self):
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    def free_pct(self, path):
        value = os.statvfs(path)
        return value.f_bavail * 100 / value.f_blocks
    def compose_version(self):
        return subprocess.check_output(["docker", "compose", "version", "--short"], text=True).strip()
    def path_exists(self, path): return Path(path).exists()
    def config_hash(self, release_dir, project, service): return subprocess.check_output(["docker", "compose", "-p", project, "-f", str(Path(release_dir) / "app.json"), "config", "--hash", service], text=True).strip()
    def _container_id(self, project, service):
        return next(iter(subprocess.check_output(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}", "--filter", f"label=com.docker.compose.service={service}", "--format", "{{.ID}}"], text=True, stderr=subprocess.DEVNULL).splitlines()), None)
    def _inspect(self, service, project="homelab"):
        return json.loads(subprocess.check_output(["docker", "inspect", identifier], text=True, stderr=subprocess.DEVNULL))[0] if (identifier := self._container_id(project, service)) else None
    def container(self, project, service):
        try:
            inspected = self._inspect(service, project)
            if inspected is None: return None
        except (OSError, subprocess.CalledProcessError, ValueError, IndexError): return None
        config, state = inspected.get("Config", {}), inspected.get("State", {})
        labels, repo_digests = config.get("Labels", {}) or {}, inspected.get("RepoDigests", ())
        if not repo_digests and inspected.get("Image"):
            try: repo_digests = json.loads(subprocess.check_output(["docker", "image", "inspect", inspected["Image"]], text=True, stderr=subprocess.DEVNULL))[0].get("RepoDigests", ())
            except (OSError, subprocess.CalledProcessError, ValueError, IndexError): pass
        image_digest = next((value.split("@", 1)[1] for value in repo_digests if "@" in value), None)
        return ContainerView(labels.get("com.docker.compose.config-hash"), state.get("Status", ""), state.get("Error", ""), image_digest or inspected.get("Image"), state.get("Health", {}).get("Status", "none"))
    def pull(self, release_dir, services):
        if services:
            subprocess.run(["docker", "compose", "-f", str(Path(release_dir) / "app.json"),
                            "pull", *services], check=True)
    def stop(self, service):
        if (identifier := self._container_id("homelab", service)) is not None:
            subprocess.run(["docker", "stop", identifier], check=True)
    def start(self, service):
        if (identifier := self._container_id("homelab", service)) is not None:
            subprocess.run(["docker", "start", identifier], check=True)
    def run(self, argv): subprocess.run(argv, check=True)
    def healthy(self, service, timeout_s):
        deadline = time.monotonic() + timeout_s
        while True:
            project = getattr(self, "_project", "homelab")
            inspected = self._inspect(service) if project == "homelab" else self._inspect(service, project)
            state = inspected.get("State", {}) if inspected else {}
            if state.get("Status") == "running" and state.get("Health", {}).get("Status", "none") in ("healthy", "none"): return True
            remaining = deadline - time.monotonic()
            if remaining <= 0: return False
            time.sleep(min(1, remaining))
    def probe(self, service):
        project = getattr(self, "_project", "homelab")
        inspected = self._inspect(service) if project == "homelab" else self._inspect(service, project)
        if inspected is None: return False
        url = (inspected.get("Config", {}).get("Labels") or {}).get("homelab.probe", "")
        networks = inspected.get("NetworkSettings", {}).get("Networks", {})
        ip = next((network.get("IPAddress") for network in networks.values() if network.get("IPAddress")), None)
        if not url or not ip: return False
        try:
            with urllib.request.urlopen(url.replace("http://:", f"http://{ip}:", 1), timeout=10) as response:
                return 200 <= response.status < 400
        except (OSError, ValueError): return False
    def snapshot(self, service, tag): return subprocess.check_output(["homelab-backup", "--service", service, "--tag", tag], text=True).splitlines()[-1]
    def restore(self, service, tag): subprocess.run(["homelab-backup", "--restore", "--service", service, "--tag", tag], check=True)
    def remove(self, service):
        if (identifier := self._container_id("homelab", service)) is not None:
            subprocess.run(["docker", "rm", "-f", identifier], check=True)
