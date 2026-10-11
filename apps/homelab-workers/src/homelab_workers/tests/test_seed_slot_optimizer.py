from homelab_workers.shared.seed_slot_optimizer import plan_seed_slots

FIXED_NOW = 1_700_000_000


def _torrent(
    hash_id: str,
    *,
    size: int,
    tags: str = "fictional-tracker",
    progress: float = 1.0,
    added_on: int = 1,
    num_leechs: int = 0,
    state: str = "uploading",
) -> dict:
    return {
        "hash": hash_id,
        "name": hash_id,
        "size": size,
        "tags": tags,
        "progress": progress,
        "added_on": added_on,
        "num_leechs": num_leechs,
        "state": state,
    }


def test_skips_incomplete_and_untagged() -> None:
    torrents = [
        _torrent("incomplete", size=100, progress=0.5, tags="freeleech"),
        _torrent("untagged", size=999, tags="", progress=1.0),
        _torrent("ok", size=50, tags="fictional-seed"),
    ]
    plan = plan_seed_slots(torrents, now=FIXED_NOW, slot_limit=100)
    assert plan["candidate_count"] == 1
    assert plan["resume_hashes"] == []
    assert plan["pause_hashes"] == []


def test_top_n_by_size_without_guardrails() -> None:
    torrents = [_torrent(str(i), size=i, tags="fictional-tracker") for i in range(1, 105)]
    plan = plan_seed_slots(torrents, now=FIXED_NOW, slot_limit=100)
    priority_hashes = {row["hash"] for row in plan["priority"]}
    assert priority_hashes == {str(i) for i in range(5, 105)}
    assert len(plan["pause_hashes"]) == 4


def test_recent_seed_group_bumps_and_evicts_smallest() -> None:
    torrents = [
        _torrent("small", size=10, tags="fictional-tracker", added_on=1),
        _torrent("big", size=1000, tags="fictional-tracker", added_on=2),
        _torrent(
            "fresh",
            size=5,
            tags="fictional-seed",
            added_on=FIXED_NOW - 3600,
        ),
    ]
    plan = plan_seed_slots(torrents, now=FIXED_NOW, slot_limit=2)
    priority_hashes = {row["hash"] for row in plan["priority"]}
    assert priority_hashes == {"big", "fresh"}
    assert plan["evicted"][0]["hash"] == "small"
    assert plan["pause_hashes"] == ["small"]


def test_recent_seed_tag_is_configurable() -> None:
    torrents = [
        _torrent("small", size=10, tags="custom-tracker", added_on=1),
        _torrent("big", size=1000, tags="custom-tracker", added_on=2),
        _torrent("fresh", size=5, tags="custom-seed", added_on=FIXED_NOW - 3600),
    ]
    plan = plan_seed_slots(
        torrents,
        now=FIXED_NOW,
        slot_limit=2,
        tracker_tags=frozenset({"custom-tracker", "custom-seed"}),
        recent_seed_tag="custom-seed",
    )
    assert {row["hash"] for row in plan["priority"]} == {"big", "fresh"}
    assert plan["pause_hashes"] == ["small"]


def test_leechers_guardrail_not_paused_when_demoted() -> None:
    torrents = [
        _torrent("priority", size=100, tags="fictional-tracker"),
        _torrent("demoted", size=1, tags="fictional-tracker", num_leechs=3),
    ]
    plan = plan_seed_slots(torrents, now=FIXED_NOW, slot_limit=1)
    assert "demoted" not in plan["pause_hashes"]
    assert plan["pause_hashes"] == ["priority"]


def test_demoted_zero_leechers_get_paused() -> None:
    torrents = [
        _torrent("priority", size=100, tags="fictional-tracker"),
        _torrent("demoted", size=1, tags="fictional-tracker", num_leechs=0, state="uploading"),
    ]
    plan = plan_seed_slots(torrents, now=FIXED_NOW, slot_limit=1)
    assert plan["pause_hashes"] == ["demoted"]


def test_resume_paused_priority_torrent() -> None:
    torrents = [_torrent("p", size=100, tags="fictional-tracker", state="pausedUP")]
    plan = plan_seed_slots(torrents, now=FIXED_NOW, slot_limit=100)
    assert plan["resume_hashes"] == ["p"]
    assert plan["pause_hashes"] == []


def test_fewer_than_slot_limit_never_pauses() -> None:
    torrents = [_torrent("only", size=10, tags="freeleech")]
    plan = plan_seed_slots(torrents, now=FIXED_NOW, slot_limit=100)
    assert plan["priority_count"] == 1
    assert plan["pause_hashes"] == []


class FakeQbt:
    def __init__(self) -> None:
        self.paused: list[str] = []
        self.resumed: list[str] = []

    def torrents_pause(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int:
        self.paused.extend(hashes or [])
        return len(hashes or [])

    def torrents_resume(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int:
        self.resumed.extend(hashes or [])
        return len(hashes or [])


def test_apply_seed_slots_calls_resume_then_pause() -> None:
    from homelab_workers.shared.seed_slot_optimizer import apply_seed_slots

    plan = {"resume_hashes": ["a"], "pause_hashes": ["b"]}
    client = FakeQbt()
    result = apply_seed_slots(client, plan)
    assert client.resumed == ["a"]
    assert client.paused == ["b"]
    assert result["applied_resume_count"] == 1
    assert result["applied_pause_count"] == 1
