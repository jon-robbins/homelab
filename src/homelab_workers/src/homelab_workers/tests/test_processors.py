from __future__ import annotations

from dataclasses import dataclass

import pytest

from homelab_workers.arr_retry import logic, processors


@dataclass
class _FakeArrClient:
    queue_payload: object

    def __post_init__(self) -> None:
        self.deleted: list[tuple[str, dict[str, str] | None]] = []
        self.posted: list[tuple[str, dict[str, object]]] = []

    def get(self, path: str, query: dict[str, object] | None = None):  # noqa: ANN201
        assert path == "/queue/details"
        assert query == {"all": "true"}
        return self.queue_payload

    def delete(self, path: str, query: dict[str, str] | None = None) -> None:
        self.deleted.append((path, query))

    def post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        self.posted.append((path, payload))
        return {"id": 1}


def test_processors_load_queue_returns_list_payload() -> None:
    client = _FakeArrClient(queue_payload=[{"id": 1}])

    queue = processors._load_queue(client, "sonarr")

    assert queue == [{"id": 1}]


def test_processors_load_queue_raises_on_non_list_payload() -> None:
    client = _FakeArrClient(queue_payload={"records": []})

    with pytest.raises(RuntimeError, match="Unexpected Sonarr queue payload"):
        processors._load_queue(client, "sonarr")


def test_processors_remove_stalled_queue_items_deletes_expected_ids() -> None:
    client = _FakeArrClient(queue_payload=[])
    stalled = [{"id": 22}, {"id": 33}, {"id": None}]

    processors._remove_stalled_queue_items(client, stalled, "sonarr")

    assert len(client.deleted) == 2
    assert client.deleted[0][0] == "/queue/22"
    assert client.deleted[1][0] == "/queue/33"


def test_processors_run_searches_posts_command_per_id() -> None:
    client = _FakeArrClient(queue_payload=[])

    processors._run_searches(client, [7, 9], "sonarr", "EpisodeSearch", "episodeId", "episodeIds")

    assert client.posted == [
        ("/command", {"name": "EpisodeSearch", "episodeIds": [7]}),
        ("/command", {"name": "EpisodeSearch", "episodeIds": [9]}),
    ]


def test_logic_queue_item_looks_stalled_detects_tracker_failure() -> None:
    item = {
        "protocol": "torrent",
        "status": "downloading",
        "trackedDownloadState": "importPending",
        "statusMessages": [{"title": "Tracker warning", "messages": "No peers available"}],
    }

    assert logic.queue_item_looks_stalled(item) is True
