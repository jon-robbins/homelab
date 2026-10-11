from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Protocol

ARR_IMPORT_EVENT = "downloadFolderImported"
ARR_IMPORT_EVENTS = frozenset({ARR_IMPORT_EVENT, "movieFileImported"})
SONARR_LIBRARY_CATEGORIES = frozenset({"tv-sonarr", "sonarr"})
RADARR_LIBRARY_CATEGORIES = frozenset({"movies-radarr", "radarr", "movies"})
LIBRARY_CATEGORIES = SONARR_LIBRARY_CATEGORIES | RADARR_LIBRARY_CATEGORIES


class _PostImportQbt(Protocol):
    def torrents_pause(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int: ...

    def torrents_resume(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int: ...

    def torrents_set_location(self, hashes: list[str], location: str) -> None: ...

    def torrents_recheck(self, hashes: list[str]) -> None: ...


def library_path_from_history_row(row: dict[str, Any]) -> str | None:
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    imported_path = data.get("importedPath")
    if isinstance(imported_path, str) and imported_path.strip():
        return imported_path.strip()
    for key in ("episodeFile", "movieFile"):
        file_row = data.get(key)
        if isinstance(file_row, dict):
            path = file_row.get("path") or file_row.get("relativePath")
            if isinstance(path, str) and path.strip():
                return path.strip()
    return None


def is_nvme_staging_path(path: str, *, nvme_downloads_prefix: str) -> bool:
    normalized = path.rstrip("/")
    prefix = nvme_downloads_prefix.rstrip("/")
    return normalized == prefix or normalized.startswith(prefix + "/")


def container_to_host_path(
    path: str,
    *,
    downloads_container: str,
    downloads_host: Path,
    tv_container: str,
    tv_host: Path,
    movies_container: str,
    movies_host: Path,
) -> Path:
    normalized = path if path.startswith("/") else f"/{path}"
    if normalized == downloads_container or normalized.startswith(downloads_container + "/"):
        rel = normalized[len(downloads_container) :].lstrip("/")
        return downloads_host / rel
    if normalized == tv_container or normalized.startswith(tv_container + "/"):
        rel = normalized[len(tv_container) :].lstrip("/")
        return tv_host / rel
    if normalized == movies_container or normalized.startswith(movies_container + "/"):
        rel = normalized[len(movies_container) :].lstrip("/")
        return movies_host / rel
    return Path(normalized)


def _torrent_by_hash(torrents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for torrent in torrents:
        raw = torrent.get("hash")
        if isinstance(raw, str) and raw:
            out[raw.lower()] = torrent
    return out


def plan_post_import_relocate(
    *,
    history_rows: list[dict[str, Any]],
    torrents: list[dict[str, Any]],
    processed_ids: set[str],
    nvme_downloads_prefix: str,
    library_prefixes: tuple[str, ...] = ("/tv", "/movies"),
) -> list[dict[str, Any]]:
    by_hash = _torrent_by_hash(torrents)
    actions: list[dict[str, Any]] = []
    for row in history_rows:
        if row.get("eventType") not in ARR_IMPORT_EVENTS:
            continue
        download_id = row.get("downloadId")
        if not isinstance(download_id, str) or not download_id:
            continue
        download_key = download_id.lower()
        if download_key in processed_ids:
            continue
        library_file = library_path_from_history_row(row)
        if not library_file:
            continue
        if not any(
            library_file.startswith(prefix + "/") or library_file == prefix for prefix in library_prefixes
        ):
            continue
        torrent = by_hash.get(download_key)
        if not torrent:
            continue
        category = str(torrent.get("category") or "").lower()
        if category not in LIBRARY_CATEGORIES:
            continue
        content_path = str(torrent.get("content_path") or "")
        if not is_nvme_staging_path(content_path, nvme_downloads_prefix=nvme_downloads_prefix):
            continue
        library_dir = str(Path(library_file).parent)
        actions.append(
            {
                "hash": download_key,
                "download_id": download_key,
                "category": category,
                "library_file": library_file,
                "set_location_dir": library_dir,
                "staging_path": content_path,
            }
        )
    return actions


def apply_post_import_relocate(
    qbt: _PostImportQbt,
    actions: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    path_map: dict[str, Path] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for action in actions:
        hash_value = str(action.get("hash") or "")
        library_file = str(action.get("library_file") or "")
        set_location_dir = str(action.get("set_location_dir") or "")
        staging_path = str(action.get("staging_path") or "")
        row: dict[str, Any] = {
            "hash": hash_value,
            "library_file": library_file,
            "set_location_dir": set_location_dir,
            "staging_path": staging_path,
            "dry_run": dry_run,
        }
        if dry_run:
            row["ok"] = True
            row["planned"] = True
            results.append(row)
            continue
        paused = False
        try:
            library_host = (
                path_map["library_file_host"]
                if path_map and "library_file_host" in path_map
                else Path(library_file)
            )
            staging_host = (
                path_map["staging_host"] if path_map and "staging_host" in path_map else Path(staging_path)
            )
            if not library_host.exists():
                raise FileNotFoundError(f"library file missing: {library_host}")
            if not hash_value or not set_location_dir:
                raise ValueError("action missing hash or set_location_dir")
            qbt.torrents_pause(hashes=[hash_value])
            paused = True
            if staging_host.exists() and staging_host.resolve() != library_host.resolve():
                if staging_host.is_dir():
                    shutil.rmtree(staging_host)
                else:
                    staging_host.unlink(missing_ok=True)
            qbt.torrents_set_location([hash_value], set_location_dir)
            qbt.torrents_recheck([hash_value])
            qbt.torrents_resume(hashes=[hash_value])
            paused = False
            row["ok"] = True
        except Exception as exc:
            row["ok"] = False
            row["error"] = str(exc)
            if paused:
                try:
                    qbt.torrents_resume(hashes=[hash_value])
                except Exception as resume_exc:
                    row["resume_error"] = str(resume_exc)
        results.append(row)
    return results
