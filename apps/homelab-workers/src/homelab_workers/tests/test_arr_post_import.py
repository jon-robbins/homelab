from __future__ import annotations

from pathlib import Path

from homelab_workers.shared.arr_post_import import (
    container_to_host_path,
    is_nvme_staging_path,
    library_path_from_history_row,
    plan_post_import_relocate,
)


def test_library_path_from_sonarr_download_folder_imported() -> None:
    row = {
        "eventType": "downloadFolderImported",
        "downloadId": "abc123",
        "data": {
            "droppedPath": "/downloads/Show S01E01.mkv",
            "importedPath": "/tv/Show/Season 01/Show S01E01.mkv",
        },
    }
    assert library_path_from_history_row(row) == "/tv/Show/Season 01/Show S01E01.mkv"


def test_library_path_from_sonarr_history_episode_file() -> None:
    row = {
        "eventType": "downloadFolderImported",
        "downloadId": "abc123",
        "data": {
            "episodeFile": {
                "relativePath": "Show/Season 01/Show S01E01.mkv",
                "path": "/tv/Show/Season 01/Show S01E01.mkv",
            }
        },
    }
    assert library_path_from_history_row(row) == "/tv/Show/Season 01/Show S01E01.mkv"


def test_library_path_from_radarr_history_movie_file() -> None:
    row = {
        "eventType": "downloadFolderImported",
        "downloadId": "def456",
        "data": {
            "movieFile": {
                "relativePath": "Movie (2020)/Movie (2020).mkv",
                "path": "/movies/Movie (2020)/Movie (2020).mkv",
            }
        },
    }
    assert library_path_from_history_row(row) == "/movies/Movie (2020)/Movie (2020).mkv"


def test_container_to_host_path_maps_downloads_tv_and_movies() -> None:
    downloads_host = Path("/mnt/media-nvme/Incoming")
    tv_host = Path("/mnt/media-hdd/TV")
    movies_host = Path("/mnt/media-hdd/Movies")
    assert container_to_host_path(
        "/downloads/tv/Show",
        downloads_container="/downloads",
        downloads_host=downloads_host,
        tv_container="/tv",
        tv_host=tv_host,
        movies_container="/movies",
        movies_host=movies_host,
    ) == downloads_host / "tv/Show"
    assert container_to_host_path(
        "/tv/Show/Season 01/S01E01.mkv",
        downloads_container="/downloads",
        downloads_host=downloads_host,
        tv_container="/tv",
        tv_host=tv_host,
        movies_container="/movies",
        movies_host=movies_host,
    ) == tv_host / "Show/Season 01/S01E01.mkv"
    assert container_to_host_path(
        "/movies/Movie (2020)/Movie (2020).mkv",
        downloads_container="/downloads",
        downloads_host=downloads_host,
        tv_container="/tv",
        tv_host=tv_host,
        movies_container="/movies",
        movies_host=movies_host,
    ) == movies_host / "Movie (2020)/Movie (2020).mkv"


def test_plan_accepts_radarr_movie_file_imported_event() -> None:
    history = [
        {
            "eventType": "movieFileImported",
            "downloadId": "b" * 40,
            "data": {"movieFile": {"path": "/movies/Movie (2020)/Movie (2020).mkv"}},
        }
    ]
    torrents = [
        {
            "hash": "b" * 40,
            "category": "movies-radarr",
            "content_path": "/downloads/movies/Movie (2020)",
            "progress": 1.0,
        }
    ]
    planned = plan_post_import_relocate(
        history_rows=history,
        torrents=torrents,
        processed_ids=set(),
        nvme_downloads_prefix="/downloads",
        library_prefixes=("/tv", "/movies"),
    )
    assert len(planned) == 1
    assert planned[0]["library_file"] == "/movies/Movie (2020)/Movie (2020).mkv"


def test_is_nvme_staging_path() -> None:
    assert is_nvme_staging_path("/downloads/tv/Show", nvme_downloads_prefix="/downloads")
    assert not is_nvme_staging_path("/tv/Show", nvme_downloads_prefix="/downloads")


def test_plan_skips_already_processed_and_non_staging() -> None:
    history = [
        {
            "eventType": "downloadFolderImported",
            "downloadId": "hash1111",
            "data": {"episodeFile": {"path": "/tv/Show/Season 01/S01E01.mkv"}},
        },
        {
            "eventType": "downloadFolderImported",
            "downloadId": "hash2222",
            "data": {"episodeFile": {"path": "/tv/Other/Season 01/S01E01.mkv"}},
        },
    ]
    torrents = [
        {
            "hash": "hash1111",
            "category": "tv-sonarr",
            "content_path": "/downloads/tv/Show",
            "progress": 1.0,
        },
        {
            "hash": "hash2222",
            "category": "tv-sonarr",
            "content_path": "/tv/Other/Season 01/S01E01.mkv",
            "progress": 1.0,
        },
    ]
    planned = plan_post_import_relocate(
        history_rows=history,
        torrents=torrents,
        processed_ids={"hash1111"},
        nvme_downloads_prefix="/downloads",
        library_prefixes=("/tv", "/movies"),
    )
    assert len(planned) == 0


def test_plan_includes_nvme_staging_torrent() -> None:
    history = [
        {
            "eventType": "downloadFolderImported",
            "downloadId": "a" * 40,
            "data": {"episodeFile": {"path": "/tv/Show/Season 01/S01E01.mkv"}},
        }
    ]
    torrents = [
        {
            "hash": "a" * 40,
            "category": "tv-sonarr",
            "content_path": "/downloads/tv/Show",
            "progress": 1.0,
        }
    ]
    planned = plan_post_import_relocate(
        history_rows=history,
        torrents=torrents,
        processed_ids=set(),
        nvme_downloads_prefix="/downloads",
        library_prefixes=("/tv", "/movies"),
    )
    assert len(planned) == 1
    assert planned[0]["hash"] == "a" * 40
    assert planned[0]["library_file"] == "/tv/Show/Season 01/S01E01.mkv"
    assert planned[0]["set_location_dir"] == "/tv/Show/Season 01"
    assert planned[0]["staging_path"] == "/downloads/tv/Show"


class FakePostImportQbt:
    def __init__(self) -> None:
        self.paused: list[str] = []
        self.resumed: list[str] = []
        self.locations: list[tuple[list[str], str]] = []
        self.rechecked: list[str] = []

    def torrents_pause(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int:
        self.paused.extend(hashes or [])
        return len(hashes or [])

    def torrents_resume(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int:
        self.resumed.extend(hashes or [])
        return len(hashes or [])

    def torrents_set_location(self, hashes: list[str], location: str) -> None:
        self.locations.append((hashes, location))

    def torrents_recheck(self, hashes: list[str]) -> None:
        self.rechecked.extend(hashes)


def test_apply_post_import_relocate_deletes_staging(tmp_path: Path) -> None:
    from homelab_workers.shared.arr_post_import import apply_post_import_relocate

    library = tmp_path / "tv" / "Show" / "Season 01"
    library.mkdir(parents=True)
    library_file = library / "S01E01.mkv"
    library_file.write_text("data", encoding="utf-8")
    staging = tmp_path / "downloads" / "tv" / "Show"
    staging.mkdir(parents=True)
    (staging / "S01E01.mkv").write_text("dup", encoding="utf-8")

    qbt = FakePostImportQbt()
    actions = [
        {
            "hash": "a" * 40,
            "library_file": str(library_file),
            "set_location_dir": str(library),
            "staging_path": str(staging),
        }
    ]
    results = apply_post_import_relocate(
        qbt,
        actions,
        dry_run=False,
        path_map={"library_file_host": library_file, "staging_host": staging},
    )
    assert results[0]["ok"] is True
    assert not staging.exists()
    assert library_file.exists()
    assert qbt.paused == ["a" * 40]
    assert qbt.resumed == ["a" * 40]
    assert qbt.rechecked == ["a" * 40]


class FailingSetLocationQbt(FakePostImportQbt):
    def torrents_set_location(self, hashes: list[str], location: str) -> None:
        raise RuntimeError("setLocation failed")


def test_apply_post_import_resumes_after_set_location_failure(tmp_path: Path) -> None:
    from homelab_workers.shared.arr_post_import import apply_post_import_relocate

    library = tmp_path / "tv" / "Show" / "Season 01"
    library.mkdir(parents=True)
    library_file = library / "S01E01.mkv"
    library_file.write_text("data", encoding="utf-8")
    staging = tmp_path / "downloads" / "tv" / "Show"
    staging.mkdir(parents=True)

    qbt = FailingSetLocationQbt()
    results = apply_post_import_relocate(
        qbt,
        [
            {
                "hash": "c" * 40,
                "library_file": str(library_file),
                "set_location_dir": str(library),
                "staging_path": str(staging),
            }
        ],
        dry_run=False,
        path_map={"library_file_host": library_file, "staging_host": staging},
    )
    assert results[0]["ok"] is False
    assert qbt.paused == ["c" * 40]
    assert qbt.resumed == ["c" * 40]
