import json
from pathlib import Path
import pytest

from telegram_cases import (
    FixtureState,
    TelegramCase,
    CASES,
)


_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "homelab_mcp"
    / "night-of-the-living-dead-fixture.json"
)


def test_fixture_state_from_dict_and_to_dict():
    data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture = FixtureState.from_dict(data)

    assert fixture.title == "Night of the Living Dead"
    assert fixture.year == 1968
    assert fixture.indexer_id == 2
    assert fixture.release_guid == "https://thepiratebay.org/description.php?id=999999"
    assert fixture.release_size_bytes == 1420000000
    assert fixture.torrent_hash == "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
    assert fixture.tag == "night-of-the-living-dead-1968-test"
    assert fixture.queue_id == 42
    assert fixture.radarr_id == 101
    assert fixture.plex_rating_key == 501
    assert fixture.to_dict() == data


def test_fixture_state_validates_hash():
    base_data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

    # Short prefix rejected
    with pytest.raises(ValueError, match="torrent_hash"):
        bad = dict(base_data, torrent_hash="a1b2c3d4")
        FixtureState.from_dict(bad)

    # Non-hex rejected
    with pytest.raises(ValueError, match="torrent_hash"):
        bad = dict(base_data, torrent_hash="z" * 40)
        FixtureState.from_dict(bad)


def test_fixture_state_validates_integer_ids():
    base_data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

    for id_field in ("indexer_id", "queue_id", "radarr_id", "plex_rating_key", "release_size_bytes"):
        with pytest.raises(ValueError, match=id_field):
            bad = dict(base_data, **{id_field: -1})
            FixtureState.from_dict(bad)

        with pytest.raises((ValueError, TypeError)):
            bad = dict(base_data, **{id_field: "not_an_int"})
            FixtureState.from_dict(bad)


def test_fixture_state_from_probe_records_success():
    records = {
        "prowlarr": [
            {
                "title": "Night of the Living Dead",
                "year": 1968,
                "indexer": "The Pirate Bay",
                "indexer_id": 2,
                "guid": "https://thepiratebay.org/description.php?id=999999",
                "size_bytes": 1420000000,
            }
        ],
        "radarr": {
            "title": "Night of the Living Dead",
            "year": 1968,
            "radarr_id": 101,
            "queue_id": 42,
        },
        "qbittorrent": {
            "name": "Night of the Living Dead (1968)",
            "hash": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
            "tag": "night-of-the-living-dead-1968-test",
        },
        "plex": {
            "title": "Night of the Living Dead",
            "year": 1968,
            "rating_key": 501,
        },
    }

    fixture = FixtureState.from_probe_records(records)
    assert fixture.title == "Night of the Living Dead"
    assert fixture.year == 1968
    assert fixture.indexer_id == 2
    assert fixture.release_guid == "https://thepiratebay.org/description.php?id=999999"
    assert fixture.release_size_bytes == 1420000000
    assert fixture.torrent_hash == "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
    assert fixture.tag == "night-of-the-living-dead-1968-test"
    assert fixture.queue_id == 42
    assert fixture.radarr_id == 101
    assert fixture.plex_rating_key == 501


def test_fixture_state_from_probe_records_rejects_missing_identity():
    records = {
        "prowlarr": [
            {
                "title": "Night of the Living Dead",
                "year": 1968,
                "indexer": "The Pirate Bay",
                "indexer_id": 2,
                "guid": "https://thepiratebay.org/description.php?id=999999",
                "size_bytes": 1420000000,
            }
        ],
        # Missing radarr, qbittorrent, plex
    }
    with pytest.raises(ValueError, match="missing"):
        FixtureState.from_probe_records(records)


def test_fixture_state_from_probe_records_rejects_duplicate_conflicting_records():
    records = {
        "prowlarr": [
            {
                "title": "Night of the Living Dead",
                "year": 1968,
                "indexer": "The Pirate Bay",
                "indexer_id": 2,
                "guid": "guid-1",
                "size_bytes": 1420000000,
            },
            {
                "title": "Night of the Living Dead",
                "year": 1968,
                "indexer": "The Pirate Bay",
                "indexer_id": 2,
                "guid": "guid-2",
                "size_bytes": 1800000000,
            },
        ],
        "radarr": {"title": "Night of the Living Dead", "year": 1968, "radarr_id": 101, "queue_id": 42},
        "qbittorrent": {"hash": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678", "tag": "night-of-the-living-dead-1968-test"},
        "plex": {"rating_key": 501},
    }
    with pytest.raises(ValueError, match="ambiguous|duplicate"):
        FixtureState.from_probe_records(records)


def test_fixture_state_from_probe_records_rejects_non_pirate_bay():
    records = {
        "prowlarr": [
            {
                "title": "Night of the Living Dead",
                "year": 1968,
                "indexer": "1337x",
                "indexer_id": 5,
                "guid": "guid-1",
                "size_bytes": 1420000000,
            }
        ],
        "radarr": {"title": "Night of the Living Dead", "year": 1968, "radarr_id": 101, "queue_id": 42},
        "qbittorrent": {"hash": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678", "tag": "night-of-the-living-dead-1968-test"},
        "plex": {"rating_key": 501},
    }
    with pytest.raises(ValueError, match="indexer|Pirate Bay"):
        FixtureState.from_probe_records(records)


def test_fixture_state_from_probe_records_rejects_fictional-tracker():
    records = {
        "prowlarr": [
            {
                "title": "Night of the Living Dead",
                "year": 1968,
                "indexer": "ExampleTracker",
                "indexer_id": 1,
                "guid": "guid-1",
                "size_bytes": 1420000000,
            }
        ],
        "radarr": {"title": "Night of the Living Dead", "year": 1968, "radarr_id": 101, "queue_id": 42},
        "qbittorrent": {"hash": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678", "tag": "night-of-the-living-dead-1968-test"},
        "plex": {"rating_key": 501},
    }
    with pytest.raises(ValueError, match="forbidden|ExampleTracker"):
        FixtureState.from_probe_records(records)


def test_telegram_case_render_command():
    data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture = FixtureState.from_dict(data)

    # Non-scoped case unchanged
    help_case = next(c for c in CASES if c.name == "help")
    assert help_case.render_command(fixture) == "/help"
    assert help_case.render_command(None) == "/help"

    # Fixture-scoped cases include exact IDs
    qbt_detail_case = next(c for c in CASES if c.name == "qBittorrent detail")
    rendered = qbt_detail_case.render_command(fixture)
    assert fixture.torrent_hash in rendered

    radarr_grab_case = next(c for c in CASES if c.name == "Radarr acquisition")
    rendered_grab = radarr_grab_case.render_command(fixture)
    assert fixture.release_guid in rendered_grab
    assert str(fixture.indexer_id) in rendered_grab
    assert str(fixture.radarr_id) in rendered_grab

    pause_case = next(c for c in CASES if c.name == "owner single-item pause")
    rendered_pause = pause_case.render_command(fixture)
    assert fixture.torrent_hash in rendered_pause

    delete_case = next(c for c in CASES if c.name == "trusted single-item delete")
    rendered_delete = delete_case.render_command(fixture)
    assert str(fixture.queue_id) in rendered_delete or fixture.torrent_hash in rendered_delete
