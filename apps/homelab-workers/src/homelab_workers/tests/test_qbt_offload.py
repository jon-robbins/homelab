from pathlib import Path

from homelab_workers.shared import qbt_offload
from homelab_workers.shared.qbt_offload import (
    list_offload_candidates,
    plan_offload_candidate,
)


def test_plan_excludes_normalized_exact_tag(tmp_path) -> None:
    nvme = tmp_path / "nvme" / "Incoming"
    hdd = tmp_path / "hdd"
    src = nvme / "movies" / "Foo"
    src.mkdir(parents=True)
    (src / "f.mkv").write_bytes(b"x")
    torrent = {
        "hash": "abc",
        "name": "Foo",
        "progress": 1.0,
        "tags": "archive, Keep-On-NVMe ",
        "category": "manual",
        "content_path": str(src),
        "size": 1,
    }

    excluded = plan_offload_candidate(
        torrent,
        nvme_mount=str(tmp_path / "nvme"),
        hdd_mount=str(hdd),
        excluded_tags=[" keep-on-nvme"],
        container_to_host=lambda path: Path(path),
    )
    partial_match = plan_offload_candidate(
        torrent,
        nvme_mount=str(tmp_path / "nvme"),
        hdd_mount=str(hdd),
        excluded_tags=["keep-on"],
        container_to_host=lambda path: Path(path),
    )

    assert excluded is None
    assert partial_match is not None
    assert partial_match["qbt_location"] == "/seeding"
    assert partial_match["destination_path"] == str(hdd / ".seed" / "Seeding" / "Foo")


def test_plan_excludes_normalized_exact_category(tmp_path) -> None:
    nvme = tmp_path / "nvme"
    hdd = tmp_path / "hdd"
    content = nvme / "Incoming" / "Manual.Release"
    content.mkdir(parents=True)
    torrent = {
        "hash": "a" * 40,
        "name": "Manual.Release",
        "progress": 1.0,
        "tags": "archive",
        "category": " Hold ",
        "content_path": str(content),
        "size": 1,
    }

    assert (
        plan_offload_candidate(
            torrent,
            nvme_mount=str(nvme),
            hdd_mount=str(hdd),
            excluded_categories=["hold"],
        )
        is None
    )
    assert (
        plan_offload_candidate(
            torrent,
            nvme_mount=str(nvme),
            hdd_mount=str(hdd),
            excluded_categories=["old"],
        )
        is not None
    )


def test_list_offload_candidates_completed_on_nvme(tmp_path) -> None:
    nvme = tmp_path / "nvme"
    hdd = tmp_path / "hdd"
    nvme.mkdir()
    hdd.mkdir()
    content = nvme / "Incoming" / "Show.S01"
    content.mkdir(parents=True)
    torrents = [
        {
            "hash": "a" * 40,
            "name": "Show.S01",
            "progress": 1.0,
            "content_path": str(content),
            "state": "uploading",
        },
        {
            "hash": "b" * 40,
            "name": "Incomplete",
            "progress": 0.5,
            "content_path": str(nvme / "Incoming" / "Incomplete"),
            "state": "downloading",
        },
    ]
    rows = list_offload_candidates(
        torrents,
        nvme_mount=str(nvme),
        hdd_mount=str(hdd),
        limit=10,
    )
    assert len(rows) == 1
    assert rows[0]["hash"] == "a" * 40
    assert rows[0]["destination_path"] == str(hdd / ".seed" / "Seeding" / "Show.S01")
    assert rows[0]["qbt_location"] == "/seeding"


def test_list_offload_candidates_skips_exclusions_before_applying_limit(tmp_path) -> None:
    nvme = tmp_path / "nvme"
    hdd = tmp_path / "hdd"
    excluded_content = nvme / "Incoming" / "Pinned.Release"
    ordinary_content = nvme / "Incoming" / "Ordinary.Release"
    excluded_content.mkdir(parents=True)
    ordinary_content.mkdir(parents=True)
    torrents = [
        {
            "hash": "a" * 40,
            "name": "Pinned.Release",
            "progress": 1.0,
            "tags": "pinned",
            "content_path": str(excluded_content),
        },
        {
            "hash": "b" * 40,
            "name": "Ordinary.Release",
            "progress": 1.0,
            "tags": "",
            "content_path": str(ordinary_content),
        },
    ]

    rows = list_offload_candidates(
        torrents,
        nvme_mount=str(nvme),
        hdd_mount=str(hdd),
        limit=1,
        excluded_tags=["PINNED"],
    )

    assert [row["hash"] for row in rows] == ["b" * 40]


class FakeQbt:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.stopped: list[str] = []
        self.started: list[str] = []
        self.locations: list[tuple[list[str], str]] = []
        self.rechecked: list[str] = []

    def torrents_pause(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int:
        self.events.append("pause")
        self.stopped.extend(hashes or [])
        return len(hashes or [])

    def torrents_resume(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int:
        self.events.append("resume")
        self.started.extend(hashes or [])
        return len(hashes or [])

    def torrents_set_location(self, hashes: list[str], location: str) -> None:
        self.events.append(f"set_location:{location}")
        self.locations.append((hashes, location))

    def torrents_recheck(self, hashes: list[str]) -> None:
        self.events.append("recheck")
        self.rechecked.extend(hashes)


def test_run_offload_moves_one_torrent(tmp_path) -> None:
    nvme = tmp_path / "nvme"
    hdd = tmp_path / "hdd"
    src = nvme / "Incoming" / "Show"
    src.mkdir(parents=True)
    (src / "file.mkv").write_text("x")
    candidate = {
        "hash": "a" * 40,
        "content_path": str(src),
        "destination_path": str(hdd / ".seed" / "Seeding" / "Show"),
        "qbt_location": "/seeding",
        "size": 1,
    }
    qbt = FakeQbt()
    results = qbt_offload.run_offload_moves(
        qbt, [candidate], dry_run=False, min_hdd_free_bytes=0
    )
    assert results[0]["ok"] is True
    assert (hdd / ".seed" / "Seeding" / "Show" / "file.mkv").exists()
    assert not src.exists()
    assert qbt.stopped == ["a" * 40]
    assert qbt.locations == [(["a" * 40], "/seeding")]
    assert qbt.rechecked == ["a" * 40]
    assert qbt.started == ["a" * 40]


def test_run_offload_moves_preserves_transaction_order(tmp_path, monkeypatch) -> None:
    src = tmp_path / "nvme" / "Incoming" / "Show"
    src.mkdir(parents=True)
    (src / "file.mkv").write_text("x")
    destination = tmp_path / "hdd" / ".seed" / "Seeding" / "Show"
    candidate = {
        "hash": "a" * 40,
        "content_path": str(src),
        "destination_path": str(destination),
        "qbt_location": "/seeding",
        "size": 1,
    }
    qbt = FakeQbt()
    real_move = qbt_offload.shutil.move

    def record_move(source: str, target: str) -> str:
        qbt.events.append("move")
        return real_move(source, target)

    monkeypatch.setattr(qbt_offload.shutil, "move", record_move)

    results = qbt_offload.run_offload_moves(
        qbt, [candidate], dry_run=False, min_hdd_free_bytes=0
    )

    assert results[0]["ok"] is True
    assert qbt.events == [
        "pause",
        "move",
        "set_location:/seeding",
        "recheck",
        "resume",
    ]


def test_run_offload_resumes_torrent_after_recheck_failure(tmp_path) -> None:
    class RecheckFailingQbt(FakeQbt):
        def torrents_recheck(self, hashes: list[str]) -> None:
            super().torrents_recheck(hashes)
            raise RuntimeError("recheck failed")

    src = tmp_path / "src"
    src.mkdir()
    candidate = {
        "hash": "a" * 40,
        "content_path": str(src),
        "destination_path": str(tmp_path / "hdd" / ".seed" / "Seeding" / "Show"),
        "qbt_location": "/seeding",
        "size": 1,
    }
    qbt = RecheckFailingQbt()

    results = qbt_offload.run_offload_moves(
        qbt, [candidate], dry_run=False, min_hdd_free_bytes=0
    )

    assert results[0]["ok"] is False
    assert results[0]["error"] == "recheck failed"
    assert qbt.started == ["a" * 40]


def test_run_offload_rejects_existing_destination(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.mkv").write_text("x")
    destination = tmp_path / "hdd" / ".seed" / "Seeding" / "Show"
    destination.mkdir(parents=True)
    candidate = {
        "hash": "a" * 40,
        "content_path": str(src),
        "destination_path": str(destination),
        "qbt_location": "/seeding",
        "size": 1,
    }
    qbt = FakeQbt()

    results = qbt_offload.run_offload_moves(
        qbt, [candidate], dry_run=False, min_hdd_free_bytes=0
    )

    assert results[0]["ok"] is False
    assert "destination already exists" in results[0]["error"]
    assert (src / "file.mkv").exists()
    assert not (destination / "src").exists()
    assert qbt.stopped == []
    assert qbt.started == []


def test_run_offload_rejects_missing_source_before_qbt_update(tmp_path) -> None:
    source = tmp_path / "nvme" / "Incoming" / "Missing"
    destination = tmp_path / "hdd" / ".seed" / "Seeding" / "Missing"
    candidate = {
        "hash": "a" * 40,
        "content_path": str(source),
        "destination_path": str(destination),
        "qbt_location": "/seeding",
        "size": 1,
    }
    qbt = FakeQbt()

    results = qbt_offload.run_offload_moves(
        qbt, [candidate], dry_run=False, min_hdd_free_bytes=0
    )

    assert results[0]["ok"] is False
    assert "source does not exist" in results[0]["error"]
    assert qbt.events == []
    assert not destination.parent.exists()


def test_run_offload_moves_dry_run(tmp_path) -> None:
    candidate = {
        "hash": "a" * 40,
        "content_path": str(tmp_path / "src"),
        "destination_path": str(tmp_path / "dest"),
        "qbt_location": "/seeding",
    }
    qbt = FakeQbt()
    results = qbt_offload.run_offload_moves(
        qbt, [candidate], dry_run=True, min_hdd_free_bytes=0
    )
    assert results[0]["ok"] is True
    assert results[0]["planned"] is True
    assert qbt.stopped == []


def test_run_offload_skips_when_hdd_full(tmp_path, monkeypatch) -> None:
    candidate = {
        "hash": "a" * 40,
        "content_path": str(tmp_path / "src"),
        "destination_path": str(tmp_path / "hdd" / ".seed" / "Seeding" / "Show"),
        "qbt_location": "/seeding",
        "size": 1,
    }
    monkeypatch.setattr(qbt_offload, "hdd_free_bytes", lambda _: 100)
    qbt = FakeQbt()

    results = qbt_offload.run_offload_moves(
        qbt, [candidate], dry_run=False, min_hdd_free_bytes=100
    )

    assert results == [
        {
            "hash": "a" * 40,
            "content_path": str(tmp_path / "src"),
            "destination_path": str(tmp_path / "hdd" / ".seed" / "Seeding" / "Show"),
            "dry_run": False,
            "ok": False,
            "blocked_reason": "hdd_full",
        }
    ]
    assert qbt.stopped == []


def test_run_staging_move_moves_complete_torrent_to_nvme_in_order(tmp_path, monkeypatch) -> None:
    hdd = tmp_path / "hdd"
    nvme = tmp_path / "nvme"
    source = hdd / "TV" / "Show" / "Season 01"
    destination = nvme / "Pinned" / "Show" / "Season 01"
    source.mkdir(parents=True)
    (source / "episode.mkv").write_text("episode")
    qbt_location = str(destination.parent)
    torrent = {
        "hash": "b" * 40,
        "progress": 1.0,
        "tags": "tv-sonarr",
        "category": "tv-sonarr",
        "size": 7,
    }
    qbt = FakeQbt()
    real_move = qbt_offload.shutil.move

    def record_move(source_path: str, destination_path: str) -> str:
        qbt.events.append("move")
        return real_move(source_path, destination_path)

    monkeypatch.setattr(qbt_offload.shutil, "move", record_move)

    result = qbt_offload.run_staging_move(
        qbt,
        torrent,
        source_path=source,
        destination_path=destination,
        qbt_location=qbt_location,
        free_space_mount=nvme,
        min_free_bytes=0,
    )

    assert result["ok"] is True
    assert (destination / "episode.mkv").read_text() == "episode"
    assert not source.exists()
    assert qbt.events == [
        "pause",
        "move",
        f"set_location:{qbt_location}",
        "recheck",
        "resume",
    ]


def test_run_staging_move_rejects_incomplete_torrent(tmp_path) -> None:
    source = tmp_path / "hdd" / "Show"
    source.mkdir(parents=True)
    qbt = FakeQbt()

    result = qbt_offload.run_staging_move(
        qbt,
        {"hash": "b" * 40, "progress": 0.5, "size": 1},
        source_path=source,
        destination_path=tmp_path / "nvme" / "Show",
        qbt_location=str(tmp_path / "nvme"),
        free_space_mount=tmp_path / "nvme",
        min_free_bytes=0,
    )

    assert result["ok"] is False
    assert result["blocked_reason"] == "incomplete"
    assert source.exists()
    assert qbt.events == []


def test_run_staging_move_reports_incomplete_before_excluded(tmp_path) -> None:
    source = tmp_path / "hdd" / "Archive" / "Release"
    source.mkdir(parents=True)
    qbt = FakeQbt()

    result = qbt_offload.run_staging_move(
        qbt,
        {
            "hash": "b" * 40,
            "progress": 0.5,
            "tags": "pinned",
            "category": "manual",
            "size": 1,
        },
        source_path=source,
        destination_path=tmp_path / "nvme" / "Release",
        qbt_location=str(tmp_path / "nvme"),
        free_space_mount=tmp_path / "nvme",
        min_free_bytes=0,
        excluded_tags=["pinned"],
    )

    assert result["ok"] is False
    assert result["blocked_reason"] == "incomplete"
    assert source.exists()
    assert qbt.events == []


def test_run_staging_move_rejects_excluded_torrent_before_mutation(tmp_path) -> None:
    source = tmp_path / "hdd" / "Archive" / "Release"
    source.mkdir(parents=True)
    qbt = FakeQbt()

    result = qbt_offload.run_staging_move(
        qbt,
        {
            "hash": "b" * 40,
            "progress": 1.0,
            "tags": "archive, Pinned ",
            "category": "manual",
            "size": 1,
        },
        source_path=source,
        destination_path=tmp_path / "nvme" / "Release",
        qbt_location=str(tmp_path / "nvme"),
        free_space_mount=tmp_path / "nvme",
        min_free_bytes=0,
        excluded_tags=[" pinned"],
    )

    assert result["ok"] is False
    assert result["blocked_reason"] == "excluded"
    assert source.exists()
    assert qbt.events == []


def test_run_staging_move_rejects_excluded_category_before_mutation(tmp_path) -> None:
    source = tmp_path / "hdd" / "Archive" / "Release"
    source.mkdir(parents=True)
    qbt = FakeQbt()

    result = qbt_offload.run_staging_move(
        qbt,
        {
            "hash": "b" * 40,
            "progress": 1.0,
            "tags": "archive",
            "category": " Hold ",
            "size": 1,
        },
        source_path=source,
        destination_path=tmp_path / "nvme" / "Release",
        qbt_location=str(tmp_path / "nvme"),
        free_space_mount=tmp_path / "nvme",
        min_free_bytes=0,
        excluded_categories=["hold"],
    )

    assert result["ok"] is False
    assert result["blocked_reason"] == "excluded"
    assert source.exists()
    assert qbt.events == []


def test_run_staging_move_rejects_destination_collision(tmp_path) -> None:
    source = tmp_path / "hdd" / "Show"
    destination = tmp_path / "nvme" / "Show"
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "source.mkv").write_text("source")
    (destination / "existing.mkv").write_text("existing")
    qbt = FakeQbt()

    result = qbt_offload.run_staging_move(
        qbt,
        {"hash": "b" * 40, "progress": 1.0, "size": 1},
        source_path=source,
        destination_path=destination,
        qbt_location=str(destination.parent),
        free_space_mount=tmp_path / "nvme",
        min_free_bytes=0,
    )

    assert result["ok"] is False
    assert "destination already exists" in result["error"]
    assert (source / "source.mkv").read_text() == "source"
    assert (destination / "existing.mkv").read_text() == "existing"
    assert qbt.events == []


def test_run_staging_move_checks_explicit_nvme_free_space(tmp_path, monkeypatch) -> None:
    source = tmp_path / "hdd" / "Show"
    nvme_mount = tmp_path / "nvme"
    source.mkdir(parents=True)
    checked_paths: list[str] = []

    def available_bytes(path: str) -> int:
        checked_paths.append(path)
        return 100

    monkeypatch.setattr(qbt_offload, "hdd_free_bytes", available_bytes)
    qbt = FakeQbt()

    result = qbt_offload.run_staging_move(
        qbt,
        {"hash": "b" * 40, "progress": 1.0, "size": 1},
        source_path=source,
        destination_path=nvme_mount / "Show",
        qbt_location=str(nvme_mount),
        free_space_mount=nvme_mount,
        min_free_bytes=100,
    )

    assert result["ok"] is False
    assert result["blocked_reason"] == "nvme_full"
    assert checked_paths == [str(nvme_mount)]
    assert source.exists()
    assert qbt.events == []


def test_run_staging_move_resumes_after_recheck_failure(tmp_path) -> None:
    class RecheckFailingQbt(FakeQbt):
        def torrents_recheck(self, hashes: list[str]) -> None:
            super().torrents_recheck(hashes)
            raise RuntimeError("reverse recheck failed")

    source = tmp_path / "hdd" / "Show"
    destination = tmp_path / "nvme" / "Show"
    source.mkdir(parents=True)
    qbt = RecheckFailingQbt()

    result = qbt_offload.run_staging_move(
        qbt,
        {"hash": "b" * 40, "progress": 1.0, "size": 1},
        source_path=source,
        destination_path=destination,
        qbt_location=str(destination.parent),
        free_space_mount=tmp_path / "nvme",
        min_free_bytes=0,
    )

    assert result["ok"] is False
    assert result["error"] == "reverse recheck failed"
    assert qbt.events[-2:] == ["recheck", "resume"]
    assert qbt.started == ["b" * 40]


def test_plan_skips_arr_library_categories(tmp_path) -> None:
    nvme = tmp_path / "nvme"
    hdd = tmp_path / "hdd"
    for index, category in enumerate(
        ("tv-sonarr", "sonarr", "movies-radarr", "radarr", "movies", "TV-Sonarr")
    ):
        content = nvme / "Incoming" / f"Show-{index}-{category}"
        content.mkdir(parents=True)
        torrent = {
            "hash": "c" * 40,
            "name": f"Show-{category}",
            "progress": 1.0,
            "category": category,
            "tags": "",
            "content_path": str(content),
            "size": 1,
            "state": "uploading",
        }
        row = plan_offload_candidate(
            torrent,
            nvme_mount=str(nvme),
            hdd_mount=str(hdd),
        )
        assert row is None, f"expected skip for category={category!r}"


def test_plan_still_offloads_non_arr_completed_on_nvme(tmp_path) -> None:
    nvme = tmp_path / "nvme"
    hdd = tmp_path / "hdd"
    content = nvme / "Incoming" / "Manual.Release"
    content.mkdir(parents=True)
    torrent = {
        "hash": "d" * 40,
        "name": "Manual.Release",
        "progress": 1.0,
        "category": "",
        "tags": "",
        "content_path": str(content),
        "size": 1,
        "state": "uploading",
    }
    row = plan_offload_candidate(
        torrent,
        nvme_mount=str(nvme),
        hdd_mount=str(hdd),
    )
    assert row is not None
    assert row["qbt_location"] == "/seeding"


def test_plan_omitted_or_empty_exclusions_are_neutral(tmp_path) -> None:
    nvme = tmp_path / "nvme"
    hdd = tmp_path / "hdd"
    content = nvme / "Incoming" / "Tagged.Release"
    content.mkdir(parents=True)
    torrent = {
        "hash": "e" * 40,
        "name": "Tagged.Release",
        "progress": 1.0,
        "category": "manual",
        "tags": "archive",
        "content_path": str(content),
        "size": 1,
    }

    omitted = plan_offload_candidate(torrent, nvme_mount=str(nvme), hdd_mount=str(hdd))
    empty = plan_offload_candidate(
        torrent,
        nvme_mount=str(nvme),
        hdd_mount=str(hdd),
        excluded_tags=[],
        excluded_categories=[],
    )

    assert omitted is not None
    assert empty == omitted
