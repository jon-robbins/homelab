import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "host/move-data.sh"


def run(root: Path, legacy: Path, *args: str, bin_dir: Path | None = None,
        extra: dict[str, str] | None = None):
    env = {**os.environ, "ROOT": str(root), **(extra or {})}
    if bin_dir:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run([str(SCRIPT), "--legacy-data", str(legacy), *args],
                          env=env, text=True, capture_output=True)


def tree(root: Path):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_copy_keeps_source_and_stops_compose_containers(tmp_path):
    root, legacy = tmp_path / "root", tmp_path / "legacy"
    data, fake = root / "srv/homelab/data", tmp_path / "bin"
    data.mkdir(parents=True)
    legacy.mkdir()
    (legacy / "svc").mkdir()
    (legacy / "svc/file").write_bytes(b"data")
    (legacy / "single").write_bytes(b"one")
    fake.mkdir()
    docker = fake / "docker"
    docker.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
                      "[ \"$1\" = ps ] && printf 'a\\nb\\n'\nexit 0\n")
    docker.chmod(0o755)
    before = tree(legacy)
    result = run(root, legacy, "svc", "other=single", bin_dir=fake,
                 extra={"DOCKER_LOG": str(tmp_path / "docker.log")})
    assert result.returncode == 0 and tree(legacy) == before
    assert tree(data / "svc") == {Path("file"): b"data"}
    assert (data / "other/single").read_bytes() == b"one"
    log = (tmp_path / "docker.log").read_text()
    assert "label=com.docker.compose.project=homelab" in log
    assert "label=com.docker.compose.service=svc" in log
    assert "stop a b" in log


def test_destination_and_space_fail_before_copy(tmp_path):
    root, legacy = tmp_path / "root", tmp_path / "legacy"
    data, fake = root / "srv/homelab/data", tmp_path / "bin"
    data.mkdir(parents=True)
    legacy.mkdir()
    (legacy / "svc").write_text("data")
    (data / "svc").mkdir()
    assert run(root, legacy, "svc").returncode == 1
    (data / "svc").rmdir()
    fake.mkdir()
    (fake / "du").write_text("#!/bin/sh\necho '100  x'\n")
    (fake / "df").write_text("#!/bin/sh\necho 'Filesystem 1024-blocks Used Available'; echo 'x 1000 0 10'\n")
    for path in (fake / "du", fake / "df"):
        path.chmod(0o755)
    result = run(root, legacy, "svc", bin_dir=fake)
    assert result.returncode == 1 and "insufficient space" in result.stderr
    assert (legacy / "svc").exists() and not (data / "svc").exists()


def test_rollback_removes_destination_and_requires_source(tmp_path):
    root, legacy = tmp_path / "root", tmp_path / "legacy"
    data = root / "srv/homelab/data"
    data.mkdir(parents=True)
    legacy.mkdir()
    (legacy / "svc").write_text("source")
    (data / "svc").mkdir()
    (data / "svc/file").write_text("copied")
    assert run(root, legacy, "svc", "--rollback").returncode == 0
    assert (legacy / "svc").read_text() == "source" and not (data / "svc").exists()
    (legacy / "svc").unlink()
    result = run(root, legacy, "svc", "--rollback")
    assert result.returncode == 1 and "source missing" in result.stderr


def test_bad_args_print_usage_and_exit_two():
    result = subprocess.run([str(SCRIPT)], text=True, capture_output=True)
    assert result.returncode == 2
    assert "usage: move-data.sh --legacy-data DIR [--rollback] SVC[=PATH]..." in result.stderr
