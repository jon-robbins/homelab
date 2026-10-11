from homelab_workers.shared.freeleech_batch import (
    partition_freeleech_batch,
    plan_freeleech_batch_actions,
)


def test_plan_ranks_by_popularity_top_n_unlimited() -> None:
    torrents = [
        {"hash": "low", "tags": "freeleech", "progress": 0.1, "state": "downloading", "popularity": 1.0},
        {"hash": "high", "tags": "freeleech", "progress": 0.1, "state": "downloading", "popularity": 99.0},
        {"hash": "mid", "tags": "freeleech", "progress": 0.1, "state": "downloading", "popularity": 50.0},
    ]
    plan = plan_freeleech_batch_actions(
        torrents, batch_size=1, tag="freeleech", throttle_bps=10240
    )
    assert plan["unlimited_hashes"] == ["high"]
    assert plan["throttled_hashes"] == ["mid", "low"]
    assert plan["pause_hashes"] == []
    assert plan["throttle_bps"] == 10240


def test_plan_resumes_stopped_incomplete_does_not_pause_excess() -> None:
    torrents = [
        {"hash": "a", "tags": "freeleech", "progress": 0.2, "state": "stoppedDL", "popularity": 10.0},
        {"hash": "b", "tags": "freeleech", "progress": 0.2, "state": "downloading", "popularity": 5.0},
    ]
    plan = plan_freeleech_batch_actions(
        torrents, batch_size=1, tag="freeleech", throttle_bps=10240
    )
    assert plan["unlimited_hashes"] == ["a"]
    assert plan["throttled_hashes"] == ["b"]
    assert plan["resume_hashes"] == ["a"]
    assert plan["pause_hashes"] == []


def test_plan_ignores_completed_and_missing_files() -> None:
    torrents = [
        {"hash": "done", "tags": "freeleech", "progress": 1.0, "state": "uploading", "popularity": 999.0},
        {"hash": "broken", "tags": "freeleech", "progress": 0.0, "state": "missingFiles", "popularity": 999.0},
        {"hash": "ok", "tags": "freeleech", "progress": 0.1, "state": "downloading", "popularity": 1.0},
    ]
    plan = plan_freeleech_batch_actions(
        torrents, batch_size=5, tag="freeleech", throttle_bps=10240
    )
    assert plan["unlimited_hashes"] == ["ok"]
    assert plan["throttled_hashes"] == []


def test_partition_exposes_unlimited_and_throttled_with_popularity() -> None:
    torrents = [
        {"hash": "a", "tags": "freeleech", "progress": 0.1, "state": "downloading", "popularity": 3.0, "name": "A", "added_on": 1, "upspeed": 0},
        {"hash": "b", "tags": "freeleech", "progress": 0.1, "state": "downloading", "popularity": 9.0, "name": "B", "added_on": 2, "upspeed": 0},
    ]
    part = partition_freeleech_batch(torrents, batch_size=1, tag="freeleech", throttle_bps=10240)
    assert part["unlimited_count"] == 1
    assert part["throttled_count"] == 1
    assert part["unlimited"][0]["hash"] == "b"
    assert part["unlimited"][0]["popularity"] == 9.0
    assert part["throttled"][0]["hash"] == "a"


class FakeQbt:
    def __init__(self, torrents):
        self._torrents = torrents
        self.resume_calls = []
        self.dl_limits = []
        self.up_limits = []

    def torrents_info_raw(self, tag=None):
        return list(self._torrents)

    def torrents_resume(self, *, hashes=None, tag=None):
        self.resume_calls.append(list(hashes or []))
        return len(hashes or [])

    def torrents_set_download_limit(self, hashes, limit):
        self.dl_limits.append((list(hashes), limit))
        return len(hashes)

    def torrents_set_upload_limit(self, hashes, limit):
        self.up_limits.append((list(hashes), limit))
        return len(hashes)


def test_apply_sets_unlimited_and_throttle_limits() -> None:
    from homelab_workers.shared.freeleech_batch import apply_freeleech_batch

    torrents = [
        {"hash": "hot", "tags": "freeleech", "progress": 0.1, "state": "stoppedDL", "popularity": 100.0},
        {"hash": "cold", "tags": "freeleech", "progress": 0.1, "state": "downloading", "popularity": 1.0},
    ]
    client = FakeQbt(torrents)
    result = apply_freeleech_batch(client, batch_size=1, tag="freeleech", throttle_bps=10240)
    assert result["unlimited_hashes"] == ["hot"]
    assert result["throttled_hashes"] == ["cold"]
    assert ["hot"] in client.resume_calls
    assert (["hot"], 0) in client.dl_limits
    assert (["hot"], 0) in client.up_limits
    assert (["cold"], 10240) in client.dl_limits
    assert (["cold"], 10240) in client.up_limits
