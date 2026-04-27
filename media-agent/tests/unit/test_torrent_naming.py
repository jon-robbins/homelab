"""Season-pack / episode / range / path matchers + extract_season_number."""

from __future__ import annotations

from app.services.torrent_naming import (
    extract_season_number,
    query_matches_torrent_name,
    season_path_matches,
    season_range_includes,
    season_request_matches_release,
)


def test_season_matcher_ignores_single_episode_release() -> None:
    assert (
        query_matches_torrent_name(
            query="Crazy Ex Girlfriend",
            torrent_name="Crazy Ex-Girlfriend S04E14 Im Finding My Bliss 720p AMZN WEB-DL",
            season=4,
        )
        is False
    )


def test_season_matcher_accepts_season_range_pack_when_in_range() -> None:
    assert (
        query_matches_torrent_name(
            query="Crazy Ex Girlfriend",
            torrent_name="Crazy Ex-Girlfriend Seasons 1-4 Complete Pack 1080p",
            season=4,
        )
        is True
    )


def test_season_matcher_accepts_exact_season_pack() -> None:
    assert (
        query_matches_torrent_name(
            query="Crazy Ex Girlfriend",
            torrent_name="Crazy Ex-Girlfriend Season 4 Complete 1080p",
            season=4,
        )
        is True
    )


def test_season_path_matcher_targets_requested_season_files() -> None:
    assert season_path_matches("Crazy Ex-Girlfriend/Season 4/Episode 01.mkv", 4) is True
    assert season_path_matches("Crazy Ex-Girlfriend/S04E14.mkv", 4) is True
    assert season_path_matches("Crazy Ex-Girlfriend/Season 1/Episode 01.mkv", 4) is False


def test_extract_season_prefers_specific_path_segment() -> None:
    path = "Crazy Ex-Girlfriend (2015) S01-S04 Season 1-4/Season 4/S04E01.mkv"
    assert extract_season_number(path) == 4


# ── season_range_includes ─────────────────────────────────────────────


def test_season_range_includes_s_format_in_range() -> None:
    assert season_range_includes("breaking.bad.s01-s05.complete.1080p", 3) is True


def test_season_range_includes_s_format_out_of_range() -> None:
    assert season_range_includes("breaking.bad.s01-s05.complete.1080p", 6) is False


def test_season_range_includes_seasons_word_in_range() -> None:
    assert season_range_includes("modern.family.seasons.1-11.complete", 4) is True


def test_season_range_includes_seasons_word_out_of_range() -> None:
    assert season_range_includes("modern.family.seasons.1-3", 5) is False


def test_season_range_includes_complete_series() -> None:
    assert season_range_includes("the.wire.complete.series.1080p", 2) is True


def test_season_range_includes_complete_collection() -> None:
    assert season_range_includes("the.wire.complete.collection", 10) is True


def test_season_range_includes_boundary_start() -> None:
    assert season_range_includes("breaking.bad.s1-s5", 1) is True


def test_season_range_includes_boundary_end() -> None:
    assert season_range_includes("breaking.bad.s1-s5", 5) is True


# ── season_request_matches_release (updated multi-season behavior) ───


def test_matches_release_multi_season_in_range() -> None:
    assert season_request_matches_release("Breaking.Bad.S01-S05.Complete.1080p", 3) is True


def test_matches_release_multi_season_out_of_range() -> None:
    assert season_request_matches_release("Breaking.Bad.S01-S02.720p", 4) is False


def test_matches_release_single_season_exact() -> None:
    assert season_request_matches_release("Breaking Bad S03 720p", 3) is True


def test_matches_release_episode_specific_rejected() -> None:
    assert season_request_matches_release("Breaking Bad S03E01 720p", 3) is False


def test_matches_release_complete_series() -> None:
    assert season_request_matches_release("Breaking Bad Complete Series", 3) is True
