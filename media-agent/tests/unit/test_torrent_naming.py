"""Season-pack / episode / range / path matchers + extract_season_number."""

from __future__ import annotations

from app.services.torrent_naming import (
    extract_season_number,
    query_matches_torrent_name,
    season_path_matches,
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


def test_season_matcher_rejects_season_range_pack() -> None:
    assert (
        query_matches_torrent_name(
            query="Crazy Ex Girlfriend",
            torrent_name="Crazy Ex-Girlfriend Seasons 1-4 Complete Pack 1080p",
            season=4,
        )
        is False
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
