from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/ci/run-compose-tests.sh"
ALL_TESTS = [
    "test_yaml_syntax.sh",
    "test_env_completeness.sh",
    "test_managed_secrets_catalog.sh",
    "test_infisical_agent_files.sh",
    "test_infisical_secrets.sh",
    "test_no_hardcoded_paths.sh",
    "test_caddy_labels.sh",
    "test_caddy_entrypoint.sh",
    "test_network_definitions.sh",
    "test_app_data_dir_paths.sh",
    "test_llm_net_ownership.sh",
    "test_host_mode_whitelist.sh",
    "test_host_ports_available.sh",
    "test_arr_missing_search_service.sh",
    "test_autobrr_compose.sh",
    "test_seerr_entrypoint.sh",
    "test_worker_anchor_equivalence.sh",
    "test_homelab_agent_service.sh",
]
LOCAL_TESTS = [
    test
    for test in ALL_TESTS
    if test not in {"test_host_ports_available.sh", "test_yaml_syntax.sh"}
]


def make_tree(
    tmp_path: Path, test_list: list[str] = ALL_TESTS
) -> tuple[Path, Path, Path]:
    runner = tmp_path / "scripts/ci/run-compose-tests.sh"
    runner.parent.mkdir(parents=True)
    shutil.copy2(RUNNER, runner)
    runner.chmod(0o755)

    export_script = runner.parent / "export-ci-secret-placeholders.sh"
    export_script.write_text(
        "managed_secret_keys() { :; }\n", encoding="utf-8"
    )

    compose_runner = tmp_path / "tests/compose/run_tests.sh"
    compose_runner.parent.mkdir(parents=True)
    compose_runner.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "printf '%s' \"$#\" >> \"$INVOCATIONS_FILE\"\n"
        "if (( $# > 0 )); then printf '\\t%s' \"$@\" >> \"$INVOCATIONS_FILE\"; fi\n"
        "printf '\\n' >> \"$INVOCATIONS_FILE\"\n"
        "if [[ ${1:-} == --list ]]; then\n"
        f"  printf '%s\\n' {' '.join(test_list)}\n"
        "  exit 0\n"
        "fi\n"
        "if (( $# > 0 )); then printf '%s\\n' \"$@\" > \"$CALLS_FILE\"; else : > \"$CALLS_FILE\"; fi\n"
        "exit \"${STUB_EXIT_CODE:-0}\"\n",
        encoding="utf-8",
    )
    compose_runner.chmod(0o755)
    calls_file = tmp_path / "calls"
    return runner, calls_file, tmp_path / "invocations"


def run_runner(
    runner: Path,
    calls_file: Path,
    invocations_file: Path,
    *arguments: str,
    exit_code: str = "0",
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        CALLS_FILE=str(calls_file),
        INVOCATIONS_FILE=str(invocations_file),
        STUB_EXIT_CODE=exit_code,
    )
    return subprocess.run(
        ["bash", str(runner), *arguments],
        cwd=runner.parents[2],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def called_arguments(calls_file: Path) -> list[str]:
    return calls_file.read_text(encoding="utf-8").splitlines() if calls_file.exists() else []


def called_invocations(invocations_file: Path) -> list[tuple[int, list[str]]]:
    invocations = []
    for line in invocations_file.read_text(encoding="utf-8").splitlines():
        count, *arguments = line.split("\t")
        invocations.append((int(count), arguments))
    return invocations


def test_no_arguments_runs_compose_runner_without_arguments(tmp_path: Path) -> None:
    runner, calls_file, invocations_file = make_tree(tmp_path)

    result = run_runner(runner, calls_file, invocations_file)

    assert result.returncode == 0
    assert called_invocations(invocations_file) == [(0, [])]


def test_local_runs_list_without_host_specific_tests(tmp_path: Path) -> None:
    runner, calls_file, invocations_file = make_tree(tmp_path)

    result = run_runner(runner, calls_file, invocations_file, "--local")

    assert result.returncode == 0
    assert called_arguments(calls_file) == LOCAL_TESTS
    assert "SKIP: test_host_ports_available.sh (production-host preflight)" in result.stdout
    assert "SKIP: test_yaml_syntax.sh (requires a Docker daemon)" in result.stdout


def test_local_with_names_runs_only_those_names(tmp_path: Path) -> None:
    runner, calls_file, invocations_file = make_tree(tmp_path)

    result = run_runner(
        runner,
        calls_file,
        invocations_file,
        "--local",
        "test_caddy_labels.sh",
        "test_network_definitions.sh",
    )

    assert result.returncode == 0
    assert called_arguments(calls_file) == [
        "test_caddy_labels.sh",
        "test_network_definitions.sh",
    ]


def test_failing_compose_runner_fails_wrapper(tmp_path: Path) -> None:
    runner, calls_file, invocations_file = make_tree(tmp_path)

    result = run_runner(runner, calls_file, invocations_file, exit_code="7")

    assert result.returncode == 7
