from __future__ import annotations

from homelab_workers.arr_retry import args as args_module


def test_args_build_parser_uses_environment_defaults(monkeypatch) -> None:
    monkeypatch.setenv("SONARR_URL", "http://env-sonarr:8989")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "env-user")

    parser = args_module.build_parser()
    parsed = parser.parse_args([])

    assert parsed.sonarr_url == "http://env-sonarr:8989"
    assert parsed.qbittorrent_username == "env-user"
    assert parsed.search_missing_monitored is True


def test_args_build_parser_supports_toggle_flags() -> None:
    parser = args_module.build_parser()
    parsed = parser.parse_args(
        [
            "--apply",
            "--no-search-missing-monitored",
            "--series-id",
            "44",
            "--movie-id",
            "55",
        ]
    )

    assert parsed.apply is True
    assert parsed.search_missing_monitored is False
    assert parsed.series_id == [44]
    assert parsed.movie_id == [55]


def test_args_parse_args_calls_project_dotenv(monkeypatch) -> None:
    marker = {"called": False}

    def _fake_load_dotenv() -> None:
        marker["called"] = True

    monkeypatch.setattr(args_module, "_load_project_dotenv", _fake_load_dotenv)
    monkeypatch.setattr("sys.argv", ["arr-retry", "--min-seeders", "3"])

    parsed = args_module.parse_args()

    assert marker["called"] is True
    assert parsed.min_seeders == 3
