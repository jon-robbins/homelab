from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable, Protocol

from homelab_workers.shared.arr_post_import import LIBRARY_CATEGORIES

COMPLETE_PROGRESS = 0.999
NVME_INCOMING_SUBDIR = "Incoming"
SEEDING_SUBDIR = Path(".seed") / "Seeding"


class _QbtOffloadClient(Protocol):
    def torrents_pause(self, *, hashes: list[str] | None = None, tag: str | None = None) -> int: ...

    def torrents_resume(
        self, *, hashes: list[str] | None = None, tag: str | None = None
    ) -> int: ...

    def torrents_set_location(self, hashes: list[str], location: str) -> None: ...

    def torrents_recheck(self, hashes: list[str]) -> None: ...


def _normalized_items(values: list[str] | None) -> set[str]:
    return {value.strip().casefold() for value in values or [] if value.strip()}


def _is_excluded(
    torrent: dict[str, Any],
    *,
    excluded_tags: list[str] | None,
    excluded_categories: list[str] | None,
) -> bool:
    torrent_tags = _normalized_items(str(torrent.get("tags") or "").split(","))
    category = str(torrent.get("category") or "").strip().casefold()
    return bool(
        torrent_tags & _normalized_items(excluded_tags)
        or category in _normalized_items(excluded_categories)
    )


def hdd_free_bytes(hdd_mount: str) -> int:
    path = Path(hdd_mount)
    while not path.exists() and path.parent != path:
        path = path.parent
    return shutil.disk_usage(path).free


def _container_to_host_path(content_path: str, nvme_mount: Path) -> Path:
    if content_path == "/downloads":
        return nvme_mount / NVME_INCOMING_SUBDIR
    if content_path.startswith("/downloads/"):
        return nvme_mount / NVME_INCOMING_SUBDIR / content_path.removeprefix("/downloads/")
    return Path(content_path)


def plan_offload_candidate(
    torrent: dict[str, Any],
    *,
    nvme_mount: str,
    hdd_mount: str,
    excluded_tags: list[str] | None = None,
    excluded_categories: list[str] | None = None,
    container_to_host: Callable[[str], Path] | None = None,
) -> dict[str, Any] | None:
    if _is_excluded(
        torrent,
        excluded_tags=excluded_tags,
        excluded_categories=excluded_categories,
    ):
        return None
    if float(torrent.get("progress") or 0) < COMPLETE_PROGRESS:
        return None
    content_path = str(torrent.get("content_path") or "")
    if not content_path or content_path == "/seeding":
        return None
    if content_path.startswith("/seeding/"):
        return None

    category = str(torrent.get("category") or "").lower()
    if category in LIBRARY_CATEGORIES:
        return None

    nvme_incoming = (Path(nvme_mount) / NVME_INCOMING_SUBDIR).resolve()
    mapper = container_to_host or (lambda path: _container_to_host_path(path, Path(nvme_mount)))
    source = mapper(content_path).resolve()
    try:
        source.relative_to(nvme_incoming)
    except ValueError:
        return None

    destination = Path(hdd_mount) / SEEDING_SUBDIR / source.name
    qbt_location = "/seeding"

    return {
        "hash": torrent.get("hash"),
        "name": str(torrent.get("name") or source.name),
        "content_path": str(source),
        "destination_path": str(destination),
        "qbt_location": qbt_location,
        "size": int(torrent.get("size") or 0),
        "state": torrent.get("state"),
    }


def list_offload_candidates(
    torrents: list[dict[str, Any]],
    *,
    nvme_mount: str,
    hdd_mount: str,
    limit: int = 10,
    excluded_tags: list[str] | None = None,
    excluded_categories: list[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for torrent in torrents:
        candidate = plan_offload_candidate(
            torrent,
            nvme_mount=nvme_mount,
            hdd_mount=hdd_mount,
            excluded_tags=excluded_tags,
            excluded_categories=excluded_categories,
        )
        if candidate is not None:
            rows.append(candidate)
        if len(rows) >= limit:
            break
    return rows


def run_move_transaction(
    qbt: _QbtOffloadClient,
    *,
    hash_value: str,
    source_path: str | Path,
    destination_path: str | Path,
    qbt_location: str,
    free_space_mount: str | Path,
    min_free_bytes: int,
    torrent_size: int = 0,
    dry_run: bool = False,
    no_space_reason: str = "insufficient_space",
) -> dict[str, Any]:
    source = str(source_path)
    destination = str(destination_path)
    row: dict[str, Any] = {
        "hash": hash_value,
        "content_path": source,
        "destination_path": destination,
        "dry_run": dry_run,
    }
    if dry_run:
        row["ok"] = True
        row["planned"] = True
        return row

    paused = False
    try:
        if not hash_value or not source or not destination or not qbt_location:
            raise ValueError("candidate missing hash, paths, or qBittorrent location")
        if hdd_free_bytes(str(free_space_mount)) - torrent_size < min_free_bytes:
            row["ok"] = False
            row["blocked_reason"] = no_space_reason
            return row
        src = Path(source)
        if not src.exists():
            raise FileNotFoundError(f"source does not exist: {src}")
        dest = Path(destination)
        if dest.exists():
            raise FileExistsError(f"destination already exists: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        qbt.torrents_pause(hashes=[hash_value])
        paused = True
        shutil.move(str(src), str(dest))
        qbt.torrents_set_location([hash_value], qbt_location)
        qbt.torrents_recheck([hash_value])
        row["ok"] = True
    except Exception as exc:
        row["ok"] = False
        row["error"] = str(exc)
    finally:
        if paused:
            try:
                qbt.torrents_resume(hashes=[hash_value])
            except Exception as exc:
                if row.get("ok"):
                    row["ok"] = False
                    row["error"] = str(exc)
                else:
                    row["resume_error"] = str(exc)
    return row


def run_staging_move(
    qbt: _QbtOffloadClient,
    torrent: dict[str, Any],
    *,
    source_path: str | Path,
    destination_path: str | Path,
    qbt_location: str,
    free_space_mount: str | Path,
    min_free_bytes: int,
    dry_run: bool = False,
    excluded_tags: list[str] | None = None,
    excluded_categories: list[str] | None = None,
) -> dict[str, Any]:
    hash_value = str(torrent.get("hash") or "")
    blocked_reason: str | None = None
    if float(torrent.get("progress") or 0) < COMPLETE_PROGRESS:
        blocked_reason = "incomplete"
    elif _is_excluded(
        torrent,
        excluded_tags=excluded_tags,
        excluded_categories=excluded_categories,
    ):
        blocked_reason = "excluded"

    if blocked_reason is not None:
        return {
            "hash": hash_value,
            "content_path": str(source_path),
            "destination_path": str(destination_path),
            "dry_run": dry_run,
            "ok": False,
            "blocked_reason": blocked_reason,
        }

    return run_move_transaction(
        qbt,
        hash_value=hash_value,
        source_path=source_path,
        destination_path=destination_path,
        qbt_location=qbt_location,
        free_space_mount=free_space_mount,
        min_free_bytes=min_free_bytes,
        torrent_size=int(torrent.get("size") or 0),
        dry_run=dry_run,
        no_space_reason="nvme_full",
    )


def run_offload_moves(
    qbt: _QbtOffloadClient,
    candidates: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    min_hdd_free_bytes: int,
    free_space_mount: str | Path | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        hash_value = str(candidate.get("hash") or "")
        content_path = str(candidate.get("content_path") or "")
        destination_path = str(candidate.get("destination_path") or "")
        qbt_location = str(candidate.get("qbt_location") or "")
        results.append(
            run_move_transaction(
                qbt,
                hash_value=hash_value,
                source_path=content_path,
                destination_path=destination_path,
                qbt_location=qbt_location,
                free_space_mount=free_space_mount or destination_path,
                min_free_bytes=min_hdd_free_bytes,
                torrent_size=int(candidate.get("size") or 0),
                dry_run=dry_run,
                no_space_reason="hdd_full",
            )
        )
    return results
