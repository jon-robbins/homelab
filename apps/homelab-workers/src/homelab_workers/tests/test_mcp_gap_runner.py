from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from homelab_workers.mcp_gap_closer.runner import (
    INBOX_BRANCH,
    PR_BASE,
    RunnerConfig,
    RunnerResult,
    _agent_id_of,
    _failed_ci_logs,
    assert_pr_targets_dev,
    build_implementer_prompt,
    run_closer,
    write_daily_report,
)


def test_agent_id_prefers_agent_id_attr() -> None:
    class _A:
        agent_id = "bc-real-id"
        id = "legacy-id"

    assert _agent_id_of(_A()) == "bc-real-id"


def test_agent_id_falls_back_to_id() -> None:
    class _A:
        id = "bc-legacy-only"

    assert _agent_id_of(_A()) == "bc-legacy-only"


def test_implementer_prompt_includes_loosened_gap_rule() -> None:
    prompt = build_implementer_prompt(date_stamp="2026-08-10")
    assert "insufficient" in prompt.lower()
    assert "shell" in prompt.lower() or "docker" in prompt.lower()
    assert "drops" in prompt.lower() or "field" in prompt.lower()


def test_dry_run_does_not_call_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"create": False}

    def boom(*args: Any, **kwargs: Any) -> None:
        called["create"] = True
        raise AssertionError("should not create agent in dry-run")

    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner.Agent",
        type("A", (), {"create": staticmethod(boom)}),
    )

    result = run_closer(
        RunnerConfig(
            api_key="x",
            repo_url="https://github.com/jon-robbins/homelab.git",
            dry_run=True,
        )
    )
    assert result.status == "dry_run"
    assert called["create"] is False


def test_refuses_merge_if_base_not_dev() -> None:
    with pytest.raises(ValueError, match="dev"):
        assert_pr_targets_dev("main")


def test_assert_pr_targets_dev_accepts_dev() -> None:
    assert_pr_targets_dev("dev")


def test_build_implementer_prompt_references_skill_and_dev() -> None:
    prompt = build_implementer_prompt(date_stamp="2026-08-10")
    assert ".cursor/skills/mcp-gap-closer" in prompt
    assert "mcp-gap-inbox-latest.json" in prompt
    assert PR_BASE in prompt
    assert "NO_GAPS" in prompt
    assert "ONLY source of truth" in prompt
    assert "Do NOT fetch, checkout, reset, merge, or rebase onto origin/dev" in prompt
    assert "this tree only" in prompt


def test_write_daily_report_writes_markdown(tmp_path: pytest.TempPathFactory) -> None:
    report_dir = tmp_path
    result = RunnerResult(status="hard_stopped", pr_url="https://github.com/x/y/pull/1", detail="ci failed")
    path = write_daily_report(date_stamp="2026-08-10", result=result, report_dir=report_dir)
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "hard_stopped" in text
    assert "pull/1" in text


@dataclass
class _FakeRunResult:
    status: str = "completed"
    result: str = "Opened PR https://github.com/jon-robbins/homelab/pull/42"


class _FakeRun:
    def __init__(self, result: _FakeRunResult | None = None) -> None:
        self._result = result or _FakeRunResult()

    def wait(self) -> _FakeRunResult:
        return self._result


class _FakeAgent:
    id = "bc-test-agent-001"

    def __init__(self, result: _FakeRunResult | None = None) -> None:
        self._result = result

    def __enter__(self) -> _FakeAgent:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def send(self, _prompt: str) -> _FakeRun:
        return _FakeRun(self._result)

    def close(self) -> None:
        return None


class _FakeAgentClass:
    create_calls: list[dict[str, Any]] = []
    resume_calls: list[tuple[Any, ...]] = []

    @classmethod
    def create(cls, **kwargs: Any) -> _FakeAgent:
        cls.create_calls.append(kwargs)
        return _FakeAgent()

    @classmethod
    def resume(cls, agent_id: str, **kwargs: Any) -> _FakeAgent:
        cls.resume_calls.append((agent_id, kwargs))
        return _FakeAgent()


def test_no_gaps_when_agent_reports_no_gaps(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAgentClass.create_calls = []
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._inbox_is_empty",
        lambda: False,
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner.Agent",
        _FakeAgentClass,
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._launch_agent",
        lambda config: ("bc-no-gaps", _FakeRunResult(status="completed", result="NO_GAPS")),
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._discover_pr_url",
        lambda config, agent_id, agent_text: None,
    )

    result = run_closer(
        RunnerConfig(api_key="x", repo_url="https://github.com/jon-robbins/homelab.git")
    )
    assert result.status == "noop_no_gaps"
    assert result.agent_id == "bc-no-gaps"


def test_merged_on_green_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    gh_calls: list[list[str]] = []

    def fake_gh(config: RunnerConfig, args: list[str]) -> str:
        gh_calls.append(args)
        if args[:2] == ["pr", "view"]:
            return '{"baseRefName":"dev","url":"https://github.com/jon-robbins/homelab/pull/42"}'
        if args[:2] == ["pr", "checks"]:
            return "ci\tpass\t0\thttps://github.com/actions/runs/1\n"
        return ""

    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._inbox_is_empty",
        lambda: False,
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._launch_agent",
        lambda config: (
            "bc-merged",
            _FakeRunResult(result="Implemented tools; PR https://github.com/jon-robbins/homelab/pull/42"),
        ),
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._discover_pr_url",
        lambda config, agent_id, agent_text: "https://github.com/jon-robbins/homelab/pull/42",
    )
    monkeypatch.setattr("homelab_workers.mcp_gap_closer.runner._run_gh", fake_gh)
    monkeypatch.setattr("homelab_workers.mcp_gap_closer.runner._poll_ci", lambda *a, **k: True)

    result = run_closer(
        RunnerConfig(api_key="x", repo_url="https://github.com/jon-robbins/homelab.git")
    )
    assert result.status == "merged"
    assert result.pr_url == "https://github.com/jon-robbins/homelab/pull/42"
    assert any(args[:3] == ["pr", "merge", "42"] for args in gh_calls)


def test_skip_merge_leaves_pr_open(monkeypatch: pytest.MonkeyPatch) -> None:
    merge_called = {"value": False}

    def fake_gh(config: RunnerConfig, args: list[str]) -> str:
        if args[:2] == ["pr", "view"]:
            return '{"baseRefName":"dev","url":"https://github.com/jon-robbins/homelab/pull/7"}'
        if args[:2] == ["pr", "merge"]:
            merge_called["value"] = True
        return ""

    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._inbox_is_empty",
        lambda: False,
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._launch_agent",
        lambda config: ("bc-open", _FakeRunResult(result="PR opened")),
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._discover_pr_url",
        lambda config, agent_id, agent_text: "https://github.com/jon-robbins/homelab/pull/7",
    )
    monkeypatch.setattr("homelab_workers.mcp_gap_closer.runner._run_gh", fake_gh)
    monkeypatch.setattr("homelab_workers.mcp_gap_closer.runner._poll_ci", lambda *a, **k: True)

    result = run_closer(
        RunnerConfig(
            api_key="x",
            repo_url="https://github.com/jon-robbins/homelab.git",
            skip_merge=True,
        )
    )
    assert result.status == "pr_open_ci_pending"
    assert merge_called["value"] is False


def test_hard_stop_writes_report(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    reports: list[Any] = []

    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._inbox_is_empty",
        lambda: False,
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._launch_agent",
        lambda config: ("bc-hard", _FakeRunResult(result="PR https://github.com/jon-robbins/homelab/pull/99")),
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._discover_pr_url",
        lambda config, agent_id, agent_text: "https://github.com/jon-robbins/homelab/pull/99",
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._run_gh",
        lambda config, args: '{"baseRefName":"dev","url":"https://github.com/jon-robbins/homelab/pull/99"}'
        if args[:2] == ["pr", "view"]
        else "",
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._poll_ci",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._resume_agent_for_ci",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner.write_daily_report",
        lambda **kwargs: reports.append(kwargs) or tmp_path / "report.md",
    )

    result = run_closer(
        RunnerConfig(api_key="x", repo_url="https://github.com/jon-robbins/homelab.git", poll_seconds=1)
    )
    assert result.status == "hard_stopped"
    assert reports
    assert result.pr_url == "https://github.com/jon-robbins/homelab/pull/99"


def test_dry_run_mentions_inbox_branch() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._inbox_is_empty",
        lambda: False,
    )
    try:
        result = run_closer(
            RunnerConfig(
                api_key="x",
                repo_url="https://github.com/jon-robbins/homelab.git",
                dry_run=True,
            )
        )
        assert INBOX_BRANCH in result.detail
    finally:
        monkeypatch.undo()


def test_empty_inbox_skips_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pack_path = tmp_path / "latest.json"
    pack_path.write_text(json.dumps({"chats": []}), encoding="utf-8")
    launch_called = {"value": False}

    def boom(*args: Any, **kwargs: Any) -> tuple[str, Any]:
        launch_called["value"] = True
        raise AssertionError("should not launch agent for empty inbox")

    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._local_pack_path",
        lambda: pack_path,
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._launch_agent",
        boom,
    )

    result = run_closer(
        RunnerConfig(api_key="x", repo_url="https://github.com/jon-robbins/homelab.git")
    )
    assert result.status == "noop_empty_inbox"
    assert launch_called["value"] is False


def test_dry_run_mentions_empty_inbox_skip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pack_path = tmp_path / "latest.json"
    pack_path.write_text(json.dumps({"chats": []}), encoding="utf-8")
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._local_pack_path",
        lambda: pack_path,
    )

    result = run_closer(
        RunnerConfig(
            api_key="x",
            repo_url="https://github.com/jon-robbins/homelab.git",
            dry_run=True,
        )
    )
    assert result.status == "dry_run"
    assert "skip" in result.detail.lower()
    assert "empty" in result.detail.lower()


def test_pr_not_found_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._inbox_is_empty",
        lambda: False,
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._launch_agent",
        lambda config: (
            "bc-no-pr",
            _FakeRunResult(status="completed", result="Implemented tools but forgot PR link"),
        ),
    )
    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner._discover_pr_url",
        lambda config, agent_id, agent_text: None,
    )

    result = run_closer(
        RunnerConfig(api_key="x", repo_url="https://github.com/jon-robbins/homelab.git")
    )
    assert result.status == "error"
    assert result.agent_id == "bc-no-pr"
    assert "no PR discovered" in result.detail


def test_failed_ci_logs_uses_head_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    gh_calls: list[list[str]] = []

    def fake_gh(config: RunnerConfig, args: list[str]) -> str:
        gh_calls.append(args)
        if args[:3] == ["pr", "view", "42"]:
            return '{"headRefName":"feat/mcp-gap-20260810"}'
        if args[:2] == ["run", "list"]:
            return '[{"databaseId":999,"conclusion":"failure"}]'
        if args[:3] == ["run", "view", "999"]:
            return "failed step output"
        return ""

    monkeypatch.setattr("homelab_workers.mcp_gap_closer.runner._run_gh", fake_gh)
    config = RunnerConfig(api_key="x", repo_url="https://github.com/jon-robbins/homelab.git")
    logs = _failed_ci_logs(config, "42")
    assert logs == "failed step output"
    run_list_calls = [args for args in gh_calls if args[:2] == ["run", "list"]]
    assert run_list_calls
    assert run_list_calls[0][run_list_calls[0].index("--branch") + 1] == "feat/mcp-gap-20260810"


def test_checks_are_green_pending_exit_8(monkeypatch: pytest.MonkeyPatch) -> None:
    """gh pr checks returns exit 8 while pending; treat as not-yet-green (None)."""
    from homelab_workers.mcp_gap_closer.runner import RunnerConfig, _checks_are_green

    class _Proc:
        returncode = 8
        stdout = "validate\tpending\t0\thttps://example/actions\n"
        stderr = ""

    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner.subprocess.run",
        lambda *a, **k: _Proc(),
    )
    state = _checks_are_green(
        RunnerConfig(api_key="x", repo_url="https://github.com/jon-robbins/homelab.git"),
        "54",
    )
    assert state is None


def test_checks_are_green_pass_exit_0(monkeypatch: pytest.MonkeyPatch) -> None:
    from homelab_workers.mcp_gap_closer.runner import RunnerConfig, _checks_are_green

    class _Proc:
        returncode = 0
        stdout = "validate\tpass\t1s\thttps://example/actions\n"
        stderr = ""

    monkeypatch.setattr(
        "homelab_workers.mcp_gap_closer.runner.subprocess.run",
        lambda *a, **k: _Proc(),
    )
    state = _checks_are_green(
        RunnerConfig(api_key="x", repo_url="https://github.com/jon-robbins/homelab.git"),
        "54",
    )
    assert state is True
