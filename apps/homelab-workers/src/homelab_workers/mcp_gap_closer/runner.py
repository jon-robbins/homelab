from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_RESUMES = 5
MAX_WALL_SECONDS = 90 * 60
INBOX_BRANCH = "chore/mcp-gap-inbox"
PR_BASE = "dev"

DEFAULT_REPORT_DIR = Path("docs/superpowers/reports")
PR_URL_RE = re.compile(r"https://github\.com/[^\s)]+/pull/\d+")

# Exposed for tests; lazily resolved when the SDK is needed.
Agent: Any = None


def _import_sdk() -> tuple[Any, Any, Any, Any]:
    from cursor_sdk import Agent as SdkAgent
    from cursor_sdk import AgentOptions, CloudAgentOptions, CloudRepository

    return SdkAgent, AgentOptions, CloudAgentOptions, CloudRepository


def _sdk_agent() -> Any:
    global Agent
    if Agent is not None and getattr(Agent, "__module__", "") != __name__:
        return Agent
    Agent, _, _, _ = _import_sdk()
    return Agent


@dataclass
class RunnerConfig:
    api_key: str
    repo_url: str
    model: str = "composer-2.5"
    dry_run: bool = False
    skip_merge: bool = False
    gh_bin: str = "gh"
    poll_seconds: int = 60


@dataclass
class RunnerResult:
    status: str
    pr_url: str | None = None
    agent_id: str | None = None
    detail: str = ""


def assert_pr_targets_dev(base: str) -> None:
    if base != PR_BASE:
        raise ValueError(f"refusing merge: PR base must be {PR_BASE!r}, got {base!r}")


def build_implementer_prompt(*, date_stamp: str) -> str:
    return f"""You are the MCP gap closer for the homelab repository ({date_stamp}).

Follow `.cursor/skills/mcp-gap-closer` exactly (including checkout-lock rules).

HARD CONSTRAINTS:
1. The starting checkout (`{INBOX_BRANCH}` tip) is the ONLY source of truth for which MCP tools exist.
2. Do NOT fetch, checkout, reset, merge, or rebase onto origin/dev (or main) to inspect "what already shipped".
3. Do NOT skip a gap because you remember it from prior PRs/chats or believe it exists on remote/dev.
4. Create branch `feat/mcp-gap-{date_stamp.replace("-", "")}` from CURRENT HEAD and keep that ancestry.

Read:
- `docs/superpowers/reports/mcp-gap-inbox-latest.json`
- `docs/superpowers/reports/mcp-gap-ledger.yml`
- `apps/homelab-mcp/src/homelab_mcp/server.py` for current tools (this tree only)

A-style gap (implement when clear):
1. Operational capability was needed against the homelab stack
2. No suitable homelab-mcp tool existed in THIS checkout, OR an existing tool was clearly insufficient
   (wrong/missing fields, drops data, cannot answer the question)
3. EITHER the agent used shell / docker / ad-hoc HTTP as a workaround,
   OR the chat shows a concrete insufficient-tool failure (e.g. MCP response drops a needed field)

Skip docs-only work, SDD implementer noise, unused tools the agent simply forgot to call, and ambiguous maybes.

Implement every clear A-style gap, add tests, update the ledger, and open ONE PR targeting `{PR_BASE}`.

If there are no A-style gaps, make no code changes and reply exactly: NO_GAPS

Never merge to main. Never run docker compose or mutate live runtime.
"""


def _agent_id_of(agent: Any) -> str:
    return str(getattr(agent, "agent_id", None) or getattr(agent, "id", None) or "")


def write_daily_report(
    *,
    date_stamp: str,
    result: RunnerResult,
    report_dir: Path | None = None,
) -> Path:
    dest_dir = report_dir or DEFAULT_REPORT_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"mcp-gap-closer-{date_stamp}.md"
    lines = [
        f"# MCP Gap Closer Report — {date_stamp}",
        "",
        f"- status: `{result.status}`",
        f"- agent_id: `{result.agent_id or ''}`",
        f"- pr_url: {result.pr_url or '(none)'}",
        "",
        "## detail",
        "",
        result.detail or "(none)",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _utc_date_stamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def _detect_runtime_root() -> Path | None:
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "docker-compose.yml").is_file() and (candidate / "data").is_dir():
            return candidate
    return None


def _runtime_data() -> Path:
    return Path(os.environ.get("APP_DATA_DIR", "data")).expanduser()


def _local_pack_path() -> Path | None:
    runtime_data = _runtime_data()
    if runtime_data.is_dir() or "APP_DATA_DIR" in os.environ:
        return runtime_data / "mcp-gap-inbox" / "latest.json"
    root = _detect_runtime_root()
    if root is not None:
        return root / "data" / "mcp-gap-inbox" / "latest.json"
    return None


def _load_local_inbox_pack() -> dict | None:
    path = _local_pack_path()
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _inbox_is_empty() -> bool:
    pack = _load_local_inbox_pack()
    if pack is None:
        return True
    chats = pack.get("chats")
    return not chats


def _run_gh(config: RunnerConfig, args: list[str]) -> str:
    cmd = [config.gh_bin, *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"gh {' '.join(args)} failed ({proc.returncode}): {detail}")
    return proc.stdout


def _extract_pr_ref(pr_url: str) -> str:
    match = re.search(r"/pull/(\d+)", pr_url)
    if not match:
        raise ValueError(f"cannot parse PR number from {pr_url!r}")
    return match.group(1)


def _parse_pr_url_from_text(text: str) -> str | None:
    match = PR_URL_RE.search(text or "")
    return match.group(0) if match else None


def _discover_pr_url(config: RunnerConfig, agent_id: str, agent_text: str) -> str | None:
    from_url = _parse_pr_url_from_text(agent_text)
    if from_url:
        return from_url

    try:
        listing = _run_gh(
            config,
            [
                "pr",
                "list",
                "--base",
                PR_BASE,
                "--search",
                f"head:{agent_id}",
                "--json",
                "url,number",
                "--limit",
                "1",
            ],
        )
        rows = json.loads(listing or "[]")
        if rows:
            return str(rows[0]["url"])
    except (RuntimeError, json.JSONDecodeError, KeyError, IndexError):
        pass

    try:
        listing = _run_gh(
            config,
            [
                "pr",
                "list",
                "--base",
                PR_BASE,
                "--state",
                "open",
                "--json",
                "url,number,headRefName",
                "--limit",
                "20",
            ],
        )
        rows = json.loads(listing or "[]")
        for row in rows:
            head = str(row.get("headRefName") or "")
            if head.startswith("feat/mcp-gap-"):
                return str(row["url"])
    except (RuntimeError, json.JSONDecodeError, KeyError):
        pass

    return None


def _pr_base_ref(config: RunnerConfig, pr_ref: str) -> str:
    payload = _run_gh(config, ["pr", "view", pr_ref, "--json", "baseRefName,url"])
    data = json.loads(payload)
    return str(data["baseRefName"])


def _checks_are_green(config: RunnerConfig, pr_ref: str) -> bool | None:
    # `gh pr checks` exits 0=pass, 1=fail, 8=pending — stdout still has the table.
    proc = subprocess.run(
        [config.gh_bin, "pr", "checks", pr_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout or ""
    if proc.returncode not in (0, 1, 8) and not output.strip():
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"gh pr checks {pr_ref} failed ({proc.returncode}): {detail}")
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    states: list[str] = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 2:
            states.append(parts[1].strip().lower())
    if not states:
        return None
    if any(state in {"fail", "failed", "error", "cancelled", "timed_out"} for state in states):
        return False
    if all(state in {"pass", "passed", "success", "skipped", "neutral"} for state in states):
        return True
    return None


def _failed_ci_logs(config: RunnerConfig, pr_ref: str) -> str:
    try:
        pr_payload = _run_gh(config, ["pr", "view", pr_ref, "--json", "headRefName"])
        head_branch = str(json.loads(pr_payload)["headRefName"])
        runs = _run_gh(
            config,
            [
                "run",
                "list",
                "--branch",
                head_branch,
                "--limit",
                "1",
                "--json",
                "databaseId,conclusion",
            ],
        )
        rows = json.loads(runs or "[]")
        if not rows:
            return "CI failed; no workflow runs found."
        run_id = str(rows[0]["databaseId"])
        return _run_gh(config, ["run", "view", run_id, "--log-failed"])
    except Exception as exc:  # noqa: BLE001 — best-effort log collection
        return f"CI failed; could not fetch logs: {exc}"


def _launch_agent(config: RunnerConfig) -> tuple[str, Any]:
    SdkAgent, AgentOptions, CloudAgentOptions, CloudRepository = _import_sdk()
    date_stamp = _utc_date_stamp()
    prompt = build_implementer_prompt(date_stamp=date_stamp)

    agent = SdkAgent.create(
        api_key=config.api_key,
        model=config.model,
        cloud=CloudAgentOptions(
            repos=[CloudRepository(url=config.repo_url, starting_ref=INBOX_BRANCH)],
            auto_create_pr=True,
            skip_reviewer_request=True,
        ),
    )
    agent_id = _agent_id_of(agent)
    try:
        run = agent.send(prompt)
        result = run.wait()
        return agent_id, result
    finally:
        close = getattr(agent, "close", None)
        if callable(close):
            close()


def _resume_agent_for_ci(config: RunnerConfig, agent_id: str, logs: str) -> bool:
    SdkAgent, AgentOptions, _, _ = _import_sdk()
    follow_up = (
        "CI is failing on your PR. Fix the failures, push updates, and keep the PR targeting "
        f"`{PR_BASE}` only.\n\nFailing logs:\n```\n{logs[-12000:]}\n```"
    )
    agent = SdkAgent.resume(agent_id, AgentOptions(api_key=config.api_key))
    try:
        run = agent.send(follow_up)
        result = run.wait()
        return getattr(result, "status", "") != "error"
    finally:
        close = getattr(agent, "close", None)
        if callable(close):
            close()


def _poll_ci(
    config: RunnerConfig,
    *,
    pr_ref: str,
    agent_id: str,
    started_at: float,
) -> bool:
    resumes = 0
    while True:
        elapsed = time.monotonic() - started_at
        if elapsed >= MAX_WALL_SECONDS:
            return False

        state = _checks_are_green(config, pr_ref)
        if state is True:
            return True
        if state is False:
            if resumes >= MAX_RESUMES:
                return False
            logs = _failed_ci_logs(config, pr_ref)
            if not _resume_agent_for_ci(config, agent_id, logs):
                return False
            resumes += 1

        time.sleep(config.poll_seconds)


def _merge_pr(config: RunnerConfig, pr_ref: str) -> None:
    base = _pr_base_ref(config, pr_ref)
    assert_pr_targets_dev(base)
    _run_gh(config, ["pr", "merge", pr_ref, "--merge", "--delete-branch"])


def run_closer(config: RunnerConfig) -> RunnerResult:
    date_stamp = _utc_date_stamp()
    if config.dry_run:
        if _inbox_is_empty():
            detail = (
                "dry-run: would skip cloud agent (inbox pack missing or empty); "
                f"inbox path={_local_pack_path() or '(none)'}"
            )
        else:
            detail = (
                f"dry-run: would launch cloud agent on {INBOX_BRANCH} "
                f"for {config.repo_url} with model {config.model}"
            )
        return RunnerResult(status="dry_run", detail=detail)

    if _inbox_is_empty():
        return RunnerResult(
            status="noop_empty_inbox",
            detail=(
                "inbox pack missing or has no chats; cloud agent skipped "
                f"(path={_local_pack_path() or '(none)'})"
            ),
        )

    started_at = time.monotonic()
    try:
        agent_id, agent_result = _launch_agent(config)
    except Exception as exc:  # noqa: BLE001 — surface launch failures
        return RunnerResult(status="error", detail=f"agent launch failed: {exc}")

    agent_text = str(getattr(agent_result, "result", "") or "")
    if "NO_GAPS" in agent_text.upper():
        return RunnerResult(
            status="noop_no_gaps",
            agent_id=agent_id or None,
            detail=agent_text.strip() or "agent reported NO_GAPS",
        )

    pr_url = _discover_pr_url(config, agent_id, agent_text)
    if not pr_url:
        return RunnerResult(
            status="error",
            agent_id=agent_id,
            detail="no PR discovered after agent run",
        )

    pr_ref = _extract_pr_ref(pr_url)
    try:
        base = _pr_base_ref(config, pr_ref)
        assert_pr_targets_dev(base)
    except ValueError as exc:
        return RunnerResult(status="error", agent_id=agent_id, pr_url=pr_url, detail=str(exc))

    ci_green = _poll_ci(
        config,
        pr_ref=pr_ref,
        agent_id=agent_id,
        started_at=started_at,
    )
    if not ci_green:
        result = RunnerResult(
            status="hard_stopped",
            agent_id=agent_id,
            pr_url=pr_url,
            detail="CI babysit hit resume or wall-clock limit; PR left open",
        )
        report_path = write_daily_report(date_stamp=date_stamp, result=result)
        result.detail = f"{result.detail}; report={report_path}"
        return result

    if config.skip_merge:
        return RunnerResult(
            status="pr_open_ci_pending",
            agent_id=agent_id,
            pr_url=pr_url,
            detail="CI green; merge skipped by config",
        )

    try:
        _merge_pr(config, pr_ref)
    except Exception as exc:  # noqa: BLE001 — surface merge failures
        return RunnerResult(
            status="error",
            agent_id=agent_id,
            pr_url=pr_url,
            detail=f"merge failed: {exc}",
        )

    return RunnerResult(
        status="merged",
        agent_id=agent_id,
        pr_url=pr_url,
        detail=f"merged PR {pr_ref} into {PR_BASE}",
    )
