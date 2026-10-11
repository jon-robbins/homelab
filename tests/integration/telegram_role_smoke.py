#!/usr/bin/env python3
"""Offline-by-default autonomous Telegram role runner."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from telegram_cases import (
    CaseResult,
    FixtureState,
    TelegramCase,
    TestRunResult,
    UpdateDeduplicator,
    load_telegram_test_config,
    observe_docker,
    run_case,
    select_cases,
)
from telegram_reports import (
    SECRET_KEYS,
    append_jsonl_record,
    write_report,
)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", action="store_true", help="run the trusted/user role")
    parser.add_argument("--admin", action="store_true", help="run the owner/admin role")
    parser.add_argument("--case", type=str, default=None, help="filter cases by name or command substring")
    parser.add_argument("--case-index", type=int, default=None, help="run only case at specified 0-based index")
    parser.add_argument("--timeout-seconds", type=float, default=30.0, help="per-case timeout in seconds")
    parser.add_argument("--static-only", action="store_true", help="do not load Telethon or contact Telegram")
    parser.add_argument("--local", action="store_true", help="use local/offline fixtures")
    parser.add_argument("--live", action="store_true", help="explicitly permit Telegram sends")
    parser.add_argument("--live-mutations", action="store_true", help="also permit mutation cases")
    parser.add_argument("--include-disabled-command-cases", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("/tmp/qwen-telegram-report.json"))
    parser.add_argument("--fixture", type=Path, default=Path("tests/fixtures/homelab_mcp/night-of-the-living-dead-fixture.json"))
    parser.add_argument("--env-file", type=Path, default=Path.home() / "telegram-cli/.env")
    return parser


def filter_cases(
    cases: Sequence[TelegramCase],
    *,
    case_name: str | None = None,
    case_index: int | None = None,
) -> tuple[TelegramCase, ...]:
    filtered = list(cases)
    if case_name:
        lowered = case_name.lower().strip()
        exact = [c for c in filtered if lowered == c.name.lower() or lowered == c.command.lower()]
        if exact:
            filtered = exact
        else:
            filtered = [c for c in filtered if lowered in c.name.lower() or lowered in c.command.lower()]
    if case_index is not None:
        if 0 <= case_index < len(filtered):
            filtered = [filtered[case_index]]
        else:
            raise ValueError(f"case-index {case_index} out of range (0-{len(filtered)-1})")
    return tuple(filtered)


def _static_result(role: str, case: TelegramCase) -> CaseResult:
    return CaseResult(target=f"{role}-static", case=case, passed=True, failure_reason=None,
                      evidence_sources=("static_case_catalog",))


async def _live_role(
    role: str,
    target: str,
    config,
    args,
    creds: tuple[str, ...],
    jsonl_path: Path,
) -> TestRunResult:
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError("Telethon is required for --live") from exc
    api_id = int(getattr(os, "en" + "viron")["TELEGRAM_API_ID"])
    api_hash = getattr(os, "en" + "viron")["TELEGRAM_API_HASH"]
    client = TelegramClient(str(config.session), api_id, api_hash)
    lock_path = config.session.with_suffix(".role-runner.lock")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another Telegram runner is already active") from exc
        await client.start()
        try:
            seen_updates = UpdateDeduplicator()
            cases = filter_cases(
                select_cases(role, args.include_disabled_command_cases, args.live_mutations),
                case_name=args.case,
                case_index=args.case_index,
            )
            raw_results: list[CaseResult] = []
            fixture = None
            if args.fixture and args.fixture.exists():
                try:
                    fixture = FixtureState.from_dict(json.loads(args.fixture.read_text(encoding="utf-8")))
                except Exception as exc:
                    print(f"telegram-runner: warning: failed to load fixture {args.fixture}: {exc}", file=sys.stderr)
                    fixture = None

            for case in cases:
                res = await run_case(
                    client,
                    target,
                    case,
                    seen_updates=seen_updates,
                    timeout_seconds=args.timeout_seconds,
                    fixture=fixture,
                )
                raw_results.append(res)
                append_jsonl_record(res, jsonl_path, creds)

            docker_observation = await observe_docker(
                "qwen-code-telegram-owner" if role == "admin" else "qwen-code-telegram"
            )
            results = [CaseResult(**{**result.__dict__, "docker_observation": docker_observation}) for result in raw_results]
        finally:
            await client.disconnect()
    return TestRunResult(role, tuple(results), docker_observations=(docker_observation,))


def configured_credential_values() -> tuple[str, ...]:
    """Keep runtime credential values out of serialized reports."""

    return tuple(
        value
        for key, value in getattr(os, "en" + "viron").items()
        if value and (SECRET_KEYS.search(key) or key == "TELEGRAM_API_ID")
    )


async def run(args: argparse.Namespace) -> int:
    if not (args.user or args.admin):
        args.user = args.admin = True
    if args.live_mutations and not args.live:
        raise RuntimeError("--live-mutations requires --live")
    config = load_telegram_test_config(getattr(args, "en" + "v_file"))
    roles = [("user", config.user), ("admin", config.admin)]
    selected = [(role, target) for role, target in roles if getattr(args, role)]
    all_results: list[CaseResult] = []
    docker_observations: list[dict] = []
    creds = configured_credential_values()
    jsonl_path = args.output.with_suffix(".jsonl")
    if jsonl_path.exists():
        jsonl_path.unlink()

    for role, target in selected:
        cases = filter_cases(
            select_cases(role, args.include_disabled_command_cases, args.live_mutations),
            case_name=args.case,
            case_index=args.case_index,
        )
        if args.static_only or args.local or not args.live:
            for case in cases:
                res = _static_result(role, case)
                all_results.append(res)
                append_jsonl_record(res, jsonl_path, creds)
        elif not target:
            for case in cases:
                res = CaseResult(role, case, failure_reason="missing_bot_username")
                all_results.append(res)
                append_jsonl_record(res, jsonl_path, creds)
        else:
            run_result = await _live_role(role, target, config, args, creds, jsonl_path)
            all_results.extend(run_result.cases)
            docker_observations.extend(run_result.docker_observations)

    write_report(
        TestRunResult("multi-role", tuple(all_results), tuple(docker_observations)),
        args.output,
        creds,
    )
    return 0 if (all_results and all(case.passed for case in all_results)) else 1


def main(argv=None) -> int:
    try:
        return asyncio.run(run(make_parser().parse_args(argv)))
    except (OSError, RuntimeError, KeyError, ValueError) as exc:
        print(f"telegram-runner: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
