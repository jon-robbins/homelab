import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

import telegram_cases
from telegram_registry import catalog_validation_errors, registered_tool_names
from telegram_cases import (
    CaseResult,
    TelegramCase,
    TestRunResult,
    UpdateDeduplicator,
    extract_evidence_sources,
    load_telegram_test_config,
    observe_docker,
    run_case,
    select_cases,
)
from telegram_reports import redact, serialize_report, write_report


def test_load_config_keeps_usernames_but_not_secret_values(tmp_path: Path):
    env = tmp_path / "\x2eenv"
    env.write_text(
        "USER_BOT_USERNAME=@trusted_bot\n"
        "ADMIN_BOT_USERNAME=owner_bot\n"
        "TELEGRAM_API_HASH=super-secret\n"
        "TELEGRAM_BOT_TOKEN=123:secret\n",
        encoding="utf-8",
    )

    config = load_telegram_test_config(env)

    assert config.user == "trusted_bot"
    assert config.admin == "owner_bot"
    assert "super-secret" not in repr(config)
    assert "123:secret" not in repr(config)


def test_case_selection_has_core_cases_disabled_commands_and_attachment_rejection():
    enabled = select_cases("user")
    all_cases = select_cases("admin", include_disabled=True)

    assert {case.command for case in enabled} >= {"/help", "/status", "/clear", "/compress"}
    assert any(case.disabled for case in all_cases)
    assert not any(case.disabled for case in enabled)
    attachment_case = next(case for case in enabled if case.category == "attachment")
    assert attachment_case.attachment_disabled
    assert attachment_case.rejection_markers
    assert {case.role for case in enabled} == {"both"}
    assert any(case.role == "user" for case in select_cases("user", live_mutations=True))


def test_case_selection_rejects_unknown_role():
    with pytest.raises(ValueError, match="role"):
        select_cases("root")


def test_redact_removes_configured_and_bare_token_values_from_replies():
    value = {
        "reply": "configured-api-hash and 123456789:AAEexampleTokenValue_0123456789",
        "text": "Bearer abc123 and TELEGRAM_BOT_TOKEN=123:xyz",
        "nested": ["password=hunter2", {"api_hash": "hash-value"}],
    }

    redacted = redact(value, configured_values=("configured-api-hash",))

    assert redacted["nested"][1]["api_hash"] == "[REDACTED]"
    assert "configured-api-hash" not in json.dumps(redacted)
    assert "123456789:AAEexampleTokenValue_0123456789" not in json.dumps(redacted)
    assert "abc123" not in json.dumps(redacted)
    assert "hunter2" not in json.dumps(redacted)


def test_report_serialization_is_deterministic_and_marks_threshold_failure(tmp_path: Path):
    case = TelegramCase("diagnostic", "health", "diagnostic", ("service",), max_chars=20)
    result = CaseResult.from_reply(
        "trusted_bot",
        case,
        "service status is healthy but this is too long 123456789:AAEexampleTokenValue_0123456789",
    )
    run = TestRunResult(role="user", cases=(result,), docker_observations=())
    first = serialize_report(run, configured_values=("healthy",))
    second = serialize_report(run, configured_values=("healthy",))
    output = tmp_path / "report.json"
    write_report(run, output, configured_values=("healthy",))

    assert first == second
    assert result.failure_reason == "response_limit_exceeded"
    assert "healthy" not in first
    assert "123456789:AAEexampleTokenValue_0123456789" not in first
    assert json.loads(first)["summary"]["failed"] == 1
    assert json.loads(output.read_text(encoding="utf-8"))["cases"][0]["failure_reason"] == (
        "response_limit_exceeded"
    )


def test_duplicate_update_is_scoped_to_target_and_resets_per_run():
    case = TelegramCase("help", "/help", "command", ("help",))
    first_run = UpdateDeduplicator()
    result = CaseResult.from_reply("trusted_bot", case, "/help", update_id=7, seen_updates=first_run)
    duplicate = CaseResult.from_reply("trusted_bot", case, "/help", update_id=7, seen_updates=first_run)
    other_target = CaseResult.from_reply("owner_bot", case, "/help", update_id=7, seen_updates=first_run)
    second_run = CaseResult.from_reply(
        "trusted_bot", case, "/help", update_id=7, seen_updates=UpdateDeduplicator()
    )

    assert result.passed
    assert duplicate.failure_reason == "duplicate_update"
    assert other_target.passed
    assert second_run.passed


def test_case_result_fails_when_reply_violates_the_response_contract():
    case = TelegramCase("help", "/help", "command", ("help",))

    result = CaseResult.from_reply("trusted_bot", case, "help is available", evidence_records=())

    assert result.passed is False
    assert result.failure_reason is not None
    assert result.failure_reason.startswith("response_contract_violation:")


def test_attachment_disabled_case_requires_rejection_or_no_context_reply():
    case = next(case for case in select_cases("user") if case.category == "attachment")

    accepted = CaseResult.from_reply(
        "trusted_bot", case, "I cannot inspect an attachment because no file was provided."
    )
    invented_context = CaseResult.from_reply(
        "trusted_bot", case, "The attached \x64eployment log shows a healthy service."
    )

    assert accepted.passed
    assert invented_context.failure_reason == "attachment_context_not_rejected"


def test_extract_evidence_sources_reads_labeled_and_linked_sources():
    reply = "Evidence: homelab-mcp\nSource: qBittorrent\nhttps://status.example.test/health"

    assert extract_evidence_sources(reply) == (
        "homelab-mcp",
        "qBittorrent",
        "url:status.example.test",
    )


def test_functional_catalog_has_complete_case_metadata():
    functional = [case for case in telegram_cases.CASES if case.capability is not None]

    assert len(functional) >= 21
    assert all(case.tool for case in functional)
    assert all(case.fixture_scope for case in functional)
    assert all(case.expected_evidence for case in functional)
    assert all(case.access == ("mutation" if case.mutating else "read") for case in functional)
    assert all("tool=" + case.tool in case.expected_evidence for case in functional)
    assert not catalog_validation_errors(functional)
    assert {case.tool for case in functional} <= registered_tool_names()


def test_functional_catalog_covers_provider_operations_and_role_boundaries():
    functional = [case for case in telegram_cases.CASES if case.capability is not None]
    by_category = {category: {case.tool for case in functional if case.category == category}
                   for category in {case.category for case in functional}}

    assert by_category["qbittorrent"] >= {
        "qbt_torrents_summary", "qbt_torrents_list", "qbt_torrent_detail",
        "qbt_transfer_summary", "qbt_peer_port_verify",
    }
    assert by_category["prowlarr"] >= {"prowlarr_indexer_health", "prowlarr_indexer_search"}
    assert by_category["radarr"] >= {
        "arr_lookup", "radarr_queue_snapshot", "radarr_release_search", "radarr_release_grab",
    }
    assert "sonarr" not in by_category
    assert by_category["plex"] >= {"plex_library_search", "media_visibility_diagnosis"}
    assert by_category["autobrr"] >= {"autobrr_operational_summary", "autobrr_pipeline_diagnosis"}

    trusted_controls = {
        case.name for case in functional
        if case.category == "trusted_single_control"
    }
    assert trusted_controls == {
        "trusted single-item force start", "trusted single-item delete",
    }
    assert all(
        case.role == "user" and case.capability == "trusted-single-control"
        for case in functional if case.category == "trusted_single_control"
    )
    assert all(
        case.role == "admin" and case.access == "mutation"
        for case in functional if case.category in {"owner_mutation", "owner_admin"}
    )
    assert {
        case.tool for case in functional if case.category == "owner_mutation"
    } >= {"qbt_torrents_pause", "qbt_torrents_resume", "qbt_freeleech_batch_run"}
    assert not any(case.tool == "qbt_offload_run" for case in functional)


def test_acquisition_fixture_is_pirate_bay_only_and_scoped_to_night_of_the_living_dead():
    acquisition = [
        case for case in telegram_cases.CASES
        if case.capability == "trusted-acquisition"
    ]

    assert acquisition
    assert all(case.fixture_scope == "night-of-the-living-dead-1968" for case in acquisition)
    assert all(
        case.release_policy is not None
        and case.release_policy.required_indexer == "The Pirate Bay"
        and case.release_policy.forbidden_indexers == ("ExampleTracker",)
        for case in acquisition if case.mutating
    )


def test_release_policy_rejects_non_fixture_or_forbidden_provider_selection():
    case = next(case for case in telegram_cases.CASES if case.name == "Radarr acquisition")

    assert case.release_policy_errors({"title": "Night of the Living Dead", "year": 1968, "indexer": "The Pirate Bay"}) == ()
    assert case.release_policy_errors({"title": "Night of the Living Dead", "year": 1968, "indexer": "ExampleTracker"}) == (
        "forbidden_indexer:ExampleTracker",
    )
    assert case.release_policy_errors({"title": "Other Film", "year": 1968, "indexer": "The Pirate Bay"}) == (
        "unexpected_title:Other Film",
    )


def test_run_case_records_typing_and_reply_evidence_without_live_telegram(monkeypatch):
    class ChatAction:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeConversation:
        def __init__(self, client):
            self.client = client

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def send_message(self, _command):
            await self.client.handler(types.SimpleNamespace(user_typing=True))

        async def get_response(self):
            return types.SimpleNamespace(text="Source: homelab-mcp", id=42)

    class FakeClient:
        def add_event_handler(self, handler, _filter):
            self.handler = handler

        def remove_event_handler(self, handler):
            assert handler is self.handler

        def conversation(self, _target, **_kwargs):
            return FakeConversation(self)

    monkeypatch.setitem(sys.modules, "telethon", types.SimpleNamespace(events=types.SimpleNamespace(ChatAction=ChatAction)))
    result = asyncio.run(run_case(FakeClient(), "trusted_bot", TelegramCase("source", "status", "test")))

    assert result.typing_seen
    assert result.evidence_sources == ("homelab-mcp",)
    assert result.response_contract_errors
    assert result.failure_reason.startswith("response_contract_violation:")


def test_observe_docker_collects_health_and_bounded_logs(monkeypatch):
    calls = []

    async def fake_docker_command(*args):
        calls.append(args)
        if args[2] == "ps":
            return 0, '{"Service":"qwen-code-telegram","State":"running","Health":"healthy"}\n', ""
        return 0, "first log line\nsecond log line\n", ""

    monkeypatch.setattr(telegram_cases, "_run_docker_command", fake_docker_command)

    observation = asyncio.run(observe_docker("qwen-code-telegram"))

    assert observation["service"] == "qwen-code-telegram"
    assert observation["health"]["state"] == "running"
    assert observation["health"]["health"] == "healthy"
    assert observation["logs"]["tail"] == ["first log line", "second log line"]
    assert [call[2] for call in calls] == ["ps", "logs"]


def test_filter_cases_by_name_and_index():
    from telegram_role_smoke import filter_cases
    cases = select_cases("user")

    # Filter by name substring
    help_cases = filter_cases(cases, case_name="help")
    assert len(help_cases) == 1
    assert help_cases[0].name == "help"

    # Filter by index
    first_case = filter_cases(cases, case_index=0)
    assert len(first_case) == 1
    assert first_case[0] == cases[0]

    # Out of range index raises ValueError
    with pytest.raises(ValueError, match="out of range"):
        filter_cases(cases, case_index=999)


def test_parser_defaults_env_file_to_home_telegram_cli():
    from telegram_role_smoke import make_parser

    assert make_parser().parse_args([]).env_file == Path.home() / "telegram-cli/.env"


def test_run_case_timeout_recovery_cleans_up_handler(monkeypatch):
    handler_removed = False

    class ChatAction:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class TimingOutConversation:
        def __init__(self, client):
            self.client = client

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def send_message(self, _command):
            pass

        async def get_response(self):
            await asyncio.sleep(10.0)

    class FakeClient:
        def add_event_handler(self, handler, _filter):
            pass

        def remove_event_handler(self, handler):
            nonlocal handler_removed
            handler_removed = True

        def conversation(self, _target, **_kwargs):
            return TimingOutConversation(self)

    monkeypatch.setitem(sys.modules, "telethon", types.SimpleNamespace(events=types.SimpleNamespace(ChatAction=ChatAction)))
    result = asyncio.run(run_case(FakeClient(), "trusted_bot", TelegramCase("help", "/help", "command"), timeout_seconds=0.01))

    assert result.failure_reason == "telegram_timeout"
    assert handler_removed is True


def test_runner_incremental_jsonl_and_atomic_report(tmp_path: Path):
    from telegram_role_smoke import make_parser, run, main
    output_report = tmp_path / "final-report.json"
    env_file = tmp_path / "\x2eenv"
    env_file.write_text("USER_BOT_USERNAME=test_user_bot\nADMIN_BOT_USERNAME=test_admin_bot\n", encoding="utf-8")

    parser = make_parser()
    args = parser.parse_args([
        "--static-only",
        "--user",
        "--case", "help",
        "--output", str(output_report),
        "--env-file", str(env_file),
    ])

    exit_code = asyncio.run(run(args))
    assert exit_code == 0
    assert output_report.exists()

    jsonl_path = output_report.with_suffix(".jsonl")
    assert jsonl_path.exists()
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["case"]["name"] == "help"
    assert record["passed"] is True


def test_runner_main_exit_codes(tmp_path: Path):
    from telegram_role_smoke import main
    output_report = tmp_path / "exit-report.json"
    env_file = tmp_path / "\x2eenv"
    env_file.write_text("USER_BOT_USERNAME=test_user_bot\n", encoding="utf-8")

    # Invalid argument returns code 2
    code_invalid = main(["--case-index", "999", "--static-only", "--env-file", str(env_file), "--output", str(output_report)])
    assert code_invalid == 2

    # Normal success returns code 0
    code_ok = main(["--static-only", "--user", "--case", "help", "--env-file", str(env_file), "--output", str(output_report)])
    assert code_ok == 0
