from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Protocol

ARR_IMPORT_EVENT = "downloadFolderImported"
ARR_IMPORT_EVENTS = frozenset({ARR_IMPORT_EVENT, "movieFileImported"})
SONARR_LIBRARY_CATEGORIES = frozenset({"tv-sonarr", "sonarr"})
RADARR_LIBRARY_CATEGORIES = frozenset({"movies-radarr", "radarr", "movies"})
LIBRARY_CATEGORIES = SONARR_LIBRARY_CATEGORIES | RADARR_LIBRARY_CATEGORIES
CHECKING_STATES = frozenset({"checkingUP", "checkingDL", "checkingResumeData", "queuedForChecking", "moving"})


class _PostImportQbt(Protocol):
    def torrents_pause(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int: ...

    def torrents_resume(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int: ...

    def torrents_set_location(self, hashes: list[str], location: str) -> None: ...

    def torrents_recheck(self, hashes: list[str]) -> None: ...

    def torrent_files(self, torrent_hash: str) -> list[dict[str, Any]]: ...

    def torrents_info_raw(self, *, hashes: str | None = None) -> list[dict[str, Any]]: ...


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


def library_layout_mismatch(files: list[dict[str, Any]], library_dir: Path) -> str | None:
    """Why qBittorrent could not find this torrent's files under library_dir, or None if every file is there.

    The library usually holds only the video, while torrents often add a release folder, a sample or an .nfo.
    Pointing such a torrent at the library would leave it with missing files after its staging copy is gone.
    """
    if not files:
        return "torrent reports no files"
    for row in files:
        name = str(row.get("name") or "")
        path = library_dir / name
        if not path.is_file() or path.stat().st_size != row.get("size"):
            return f"library has no {name!r} of the torrent's size at {library_dir}"
    return None


def _wait_for_recheck(qbt: _PostImportQbt, hash_value: str, *, poll_s: float, timeout_s: float) -> None:
    # A torrent stopped before a recheck stays stopped after it, so a start sent mid-check is lost.
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(poll_s)
        rows = qbt.torrents_info_raw(hashes=hash_value)
        if rows and str(rows[0].get("state")) not in CHECKING_STATES:
            return
    raise TimeoutError(f"recheck still running after {timeout_s:.0f}s")


def _relocate(
    qbt: _PostImportQbt,
    row: dict[str, Any],
    library_host: Path,
    staging_host: Path,
    *,
    poll_s: float,
    timeout_s: float,
) -> None:
    hash_value, set_location_dir = row["hash"], row["set_location_dir"]
    qbt.torrents_pause(hashes=[hash_value])
    try:
        if staging_host.exists() and staging_host.resolve() != library_host.resolve():
            if staging_host.is_dir():
                shutil.rmtree(staging_host)
            else:
                staging_host.unlink(missing_ok=True)
        qbt.torrents_set_location([hash_value], set_location_dir)
        qbt.torrents_recheck([hash_value])
        _wait_for_recheck(qbt, hash_value, poll_s=poll_s, timeout_s=timeout_s)
    finally:
        qbt.torrents_resume(hashes=[hash_value])


def apply_post_import_relocate(
    qbt: _PostImportQbt,
    actions: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    path_map: dict[str, Path] | None = None,
    poll_s: float = 5.0,
    timeout_s: float = 1800.0,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for action in actions:
        row: dict[str, Any] = {
            "hash": str(action.get("hash") or ""),
            "library_file": str(action.get("library_file") or ""),
            "set_location_dir": str(action.get("set_location_dir") or ""),
            "staging_path": str(action.get("staging_path") or ""),
            "dry_run": dry_run,
        }
        if dry_run:
            results.append({**row, "ok": True, "planned": True})
            continue
        try:
            library_host = (path_map or {}).get("library_file_host") or Path(row["library_file"])
            staging_host = (path_map or {}).get("staging_host") or Path(row["staging_path"])
            if not library_host.exists():
                raise FileNotFoundError(f"library file missing: {library_host}")
            if not row["hash"] or not row["set_location_dir"]:
                raise ValueError("action missing hash or set_location_dir")
            mismatch = library_layout_mismatch(qbt.torrent_files(row["hash"]), library_host.parent)
            if mismatch:
                # Keep seeding from staging; done, so it is not retried every cycle.
                results.append({**row, "ok": True, "skipped": mismatch})
                continue
            _relocate(qbt, row, library_host, staging_host, poll_s=poll_s, timeout_s=timeout_s)
            row["ok"] = True
        except Exception as exc:
            row["ok"] = False
            row["error"] = str(exc)
        results.append(row)
    return results
