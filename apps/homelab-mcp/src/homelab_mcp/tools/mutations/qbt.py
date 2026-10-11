from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

import httpx
from homelab_workers.shared.profile_presets import PROFILE_NAMES, get_profile_preferences
from homelab_workers.shared.qbittorrent import QBittorrentClient
from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.arr_acquire import arr_status as worker_arr_status
from homelab_workers.shared.qbt_offload import list_offload_candidates, run_offload_moves

from homelab_mcp.audit_log import write_audit_record
from homelab_mcp.config import Settings
from homelab_mcp.envelope import error_response, ok_response
from homelab_mcp.mutation_gate import (
    canonical_offload,
    canonical_media_title_to_nvme,
    canonical_pause_hashes,
    canonical_pause_tag,
    canonical_profile,
    canonical_resume_hashes,
    canonical_resume_tag,
    require_tier1,
    require_tier2,
)
import re

_HEX_RE = re.compile(r"^[0-9a-fA-F]{8,64}$")
TOOL_TORRENTS_RESUME = "qbt_torrents_resume"
TOOL_TORRENTS_PAUSE = "qbt_torrents_pause"
TOOL_APPLY_PROFILE = "qbt_apply_profile"
TOOL_OFFLOAD_CANDIDATES = "qbt_offload_candidates"
TOOL_OFFLOAD_RUN = "qbt_offload_run"
TOOL_MEDIA_TITLE_TO_NVME_PLAN = "media_title_to_nvme_plan"
TOOL_MEDIA_TITLE_TO_NVME_STATUS = "media_title_to_nvme_status"
TOOL_TORRENT_FORCE_START = "qbt_torrent_force_start"
TOOL_EXCLUSIVE_PRIORITIZE = "qbt_exclusive_prioritize"
TOOL_EXCLUSIVE_STATUS = "qbt_exclusive_status"
TOOL_EXCLUSIVE_CANCEL = "qbt_exclusive_cancel"


def _qbt_client(settings: Settings) -> QBittorrentClient:
    return QBittorrentClient(settings.qbt_url, settings.qbt_username, settings.qbt_password)


def _check_mutations_enabled(settings: Settings, tool: str) -> str | None:
    if not settings.enable_mutations:
        return error_response(
            tool,
            code="MUTATIONS_DISABLED",
            message="Mutation tools are disabled",
            hint="Set HOMELAB_MCP_ENABLE_MUTATIONS=1 and restart homelab-mcp.",
        )
    return None


def _tier1_gate_response(
    settings: Settings,
    *,
    tool: str,
    started: float,
    expected: str,
    expected_action: str | None,
) -> str | None:
    gate_err = require_tier1(expected=expected, provided=expected_action)
    if not gate_err:
        return None
    duration_ms = int((time.perf_counter() - started) * 1000)
    write_audit_record(
        audit_path=settings.audit_path,
        tool=tool,
        tier=1,
        ok=False,
        duration_ms=duration_ms,
        error_code=gate_err["code"],
    )
    return error_response(
        tool,
        code=gate_err["code"],
        message=gate_err["message"],
        hint=gate_err.get("hint", ""),
    )


def _validate_target(
    tool: str,
    *,
    tag: str | None,
    hashes: list[str] | None,
) -> str | None:
    if hashes:
        if not all(isinstance(h, str) and _HEX_RE.match(h.strip()) for h in hashes):
            return error_response(
                tool,
                code="INVALID_ARGUMENT",
                message="All hashes must be 40 or 64 hex characters",
            )
        return None
    if tag:
        tag_clean = tag.strip()
        if tag_clean and len(tag_clean) <= 64 and "," not in tag_clean and not any(ch.isspace() for ch in tag_clean):
            return None
    return error_response(
        tool,
        code="INVALID_ARGUMENT",
        message="Provide valid full hashes or a single non-empty qBittorrent tag",
    )


def _media_stage_owner_excluded(settings: Settings, owner: dict[str, Any]) -> bool:
    labels = {str(owner.get("category") or "").strip().lower()}
    labels.update(part.strip().lower() for part in str(owner.get("tags") or "").split(","))
    return bool(labels & settings.qbt_media_stage_excluded_labels)


def _qbt_upstream_error(tool: str, exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return error_response(
            tool,
            code="UPSTREAM_TIMEOUT",
            message="qBittorrent request timed out",
        )
    if isinstance(exc, ValueError):
        return error_response(tool, code="INVALID_ARGUMENT", message=str(exc))
    return error_response(
        tool,
        code="UPSTREAM_ERROR",
        message=f"qBittorrent request failed: {exc}",
    )


def _events_http_error(tool: str, response: httpx.Response) -> str:
    try:
        body = response.json()
        upstream_error = body.get("error", response.text or f"HTTP {response.status_code}")
        message = str(upstream_error)
    except Exception:
        message = response.text or f"HTTP {response.status_code}"
    code_by_status = {
        409: "SESSION_ACTIVE",
        404: "NO_SESSION",
        400: "INVALID_ARGUMENT",
        502: "UPSTREAM_ERROR",
    }
    code = code_by_status.get(response.status_code, "UPSTREAM_ERROR")
    return error_response(tool, code=code, message=message)


def _events_upstream_error(tool: str, exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return error_response(
            tool,
            code="UPSTREAM_TIMEOUT",
            message="qbt-events request timed out",
        )
    return error_response(
        tool,
        code="UPSTREAM_ERROR",
        message=f"qbt-events request failed: {exc}",
    )


def torrents_resume(
    settings: Settings,
    *,
    tag: str | None = None,
    hashes: list[str] | None = None,
    confirm: bool = False,
) -> str:
    started = time.perf_counter()
    tool = TOOL_TORRENTS_RESUME
    tier = 2
    if not settings.enable_mutations:
        return error_response(
            tool,
            code="MUTATIONS_DISABLED",
            message="Mutation tools are disabled",
            hint="Set HOMELAB_MCP_ENABLE_MUTATIONS=1 and restart homelab-mcp.",
        )

    invalid = _validate_target(tool, tag=tag, hashes=hashes)
    if invalid:
        return invalid

    gate_err = require_tier2(confirm=confirm)
    if gate_err:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=False,
            duration_ms=duration_ms,
            error_code=gate_err["code"],
        )
        return error_response(
            tool,
            code=gate_err["code"],
            message=gate_err["message"],
            hint=gate_err.get("hint", ""),
        )

    try:
        with _qbt_client(settings) as client:
            resumed = client.torrents_resume(hashes=hashes, tag=tag)
        duration_ms = int((time.perf_counter() - started) * 1000)
        expected = (
            canonical_resume_hashes(len(hashes or []))
            if hashes
            else canonical_resume_tag(tag or "")
        )
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=True,
            duration_ms=duration_ms,
            detail={"resumed_count": resumed, "expected_action": expected},
        )
        target_str = hashes[0] if hashes and len(hashes) == 1 else (tag or "hashes")
        return ok_response(
            tool,
            {"mutation": True, "action": "resume", "target": target_str, "applied": {"resumed_count": resumed}},
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        if isinstance(exc, httpx.TimeoutException):
            return error_response(
                tool,
                code="UPSTREAM_TIMEOUT",
                message="qBittorrent request timed out",
            )
        if isinstance(exc, ValueError):
            return error_response(tool, code="INVALID_ARGUMENT", message=str(exc))
        return error_response(
            tool,
            code="UPSTREAM_ERROR",
            message=f"qBittorrent request failed: {exc}",
        )


def apply_profile(
    settings: Settings,
    *,
    profile: str,
    expected_action: str | None = None,
    extension_profiles: dict[str, dict[str, Any]] | None = None,
) -> str:
    started = time.perf_counter()
    tool = TOOL_APPLY_PROFILE
    disabled = _check_mutations_enabled(settings, tool)
    if disabled:
        return disabled
    profile_key = profile.strip()
    known_profiles = PROFILE_NAMES | (extension_profiles or {}).keys()
    if profile_key not in known_profiles:
        return error_response(
            tool,
            code="INVALID_ARGUMENT",
            message=f"unknown profile: {profile_key}",
            hint=f"Known profiles: {', '.join(sorted(known_profiles))}",
        )
    expected = canonical_profile(profile_key)
    gated = _tier1_gate_response(
        settings,
        tool=tool,
        started=started,
        expected=expected,
        expected_action=expected_action,
    )
    if gated:
        return gated
    try:
        prefs = (extension_profiles or {}).get(profile_key)
        if prefs is None:
            prefs = get_profile_preferences(profile_key)
        with _qbt_client(settings) as client:
            client.set_app_preferences(prefs)
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=True,
            duration_ms=duration_ms,
            detail={"profile": profile_key, "expected_action": expected},
        )
        return ok_response(
            tool,
            {"mutation": True, "action": "apply_profile", "target": profile_key, "applied": {"profile": profile_key, "preferences": prefs}},
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return _qbt_upstream_error(tool, exc)


def torrents_pause(
    settings: Settings,
    *,
    tag: str | None = None,
    hashes: list[str] | None = None,
    expected_action: str | None = None,
) -> str:
    started = time.perf_counter()
    tool = TOOL_TORRENTS_PAUSE
    disabled = _check_mutations_enabled(settings, tool)
    if disabled:
        return disabled
    invalid = _validate_target(tool, tag=tag, hashes=hashes)
    if invalid:
        return invalid
    expected = (
        canonical_pause_hashes(len(hashes or []))
        if hashes
        else canonical_pause_tag(tag or "")
    )
    gated = _tier1_gate_response(
        settings,
        tool=tool,
        started=started,
        expected=expected,
        expected_action=expected_action,
    )
    if gated:
        return gated
    try:
        with _qbt_client(settings) as client:
            paused = client.torrents_pause(hashes=hashes, tag=tag)
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=True,
            duration_ms=duration_ms,
            detail={"paused_count": paused, "expected_action": expected},
        )
        target_str = hashes[0] if hashes and len(hashes) == 1 else (tag or "hashes")
        return ok_response(
            tool,
            {"mutation": True, "action": "pause", "target": target_str, "applied": {"paused_count": paused}},
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return _qbt_upstream_error(tool, exc)


def offload_run(
    settings: Settings,
    *,
    limit: int = 1,
    expected_action: str | None = None,
) -> str:
    started = time.perf_counter()
    tool = TOOL_OFFLOAD_RUN
    disabled = _check_mutations_enabled(settings, tool)
    if disabled:
        return disabled
    if limit <= 0 or limit > 50:
        return error_response(tool, code="INVALID_ARGUMENT", message="limit must be between 1 and 50")
    expected = canonical_offload(limit)
    gated = _tier1_gate_response(
        settings,
        tool=tool,
        started=started,
        expected=expected,
        expected_action=expected_action,
    )
    if gated:
        return gated
    try:
        with _qbt_client(settings) as client:
            torrents = client.torrents_info_raw()
            candidates = list_offload_candidates(
                torrents,
                nvme_mount=settings.media_nvme_mount,
                hdd_mount=settings.media_hdd_mount,
                limit=limit,
            )
            results = run_offload_moves(
                client,
                candidates,
                dry_run=False,
                min_hdd_free_bytes=settings.qbt_offload_min_hdd_free_bytes,
            )
        ok_count = sum(1 for row in results if row.get("ok"))
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=True,
            duration_ms=duration_ms,
            detail={"expected_action": expected, "moved_count": ok_count, "limit": limit},
        )
        return ok_response(
            tool,
            {
                "mutation": True,
                "applied": {"limit": limit, "moved_count": ok_count, "results": results},
            },
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=1,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return _qbt_upstream_error(tool, exc)


def offload_candidates(settings: Settings, *, limit: int = 10) -> str:
    started = time.perf_counter()
    tool = TOOL_OFFLOAD_CANDIDATES
    if limit <= 0 or limit > 50:
        return error_response(
            tool,
            code="INVALID_ARGUMENT",
            message="limit must be between 1 and 50",
        )
    try:
        with _qbt_client(settings) as client:
            torrents = client.torrents_info_raw()
        rows = list_offload_candidates(
            torrents,
            nvme_mount=settings.media_nvme_mount,
            hdd_mount=settings.media_hdd_mount,
            limit=limit,
        )
        data: dict[str, Any] = {"count": len(rows), "limit": limit, "candidates": rows}
        return ok_response(tool, data, duration_ms=int((time.perf_counter() - started) * 1000))
    except Exception as exc:
        if isinstance(exc, httpx.TimeoutException):
            return error_response(tool, code="UPSTREAM_TIMEOUT", message="qBittorrent request timed out")
        return error_response(tool, code="UPSTREAM_ERROR", message=f"qBittorrent request failed: {exc}")


def media_title_to_nvme_plan(
    settings: Settings,
    *,
    media_type: str,
    query: str | None = None,
    library_id: int | None = None,
    scope: str | None = None,
    season: int | None = None,
    episode: int | None = None,
) -> str:
    """Build a read-only, gated staging plan from Arr and qBittorrent state."""
    started = time.perf_counter()
    tool = TOOL_MEDIA_TITLE_TO_NVME_PLAN
    mt = media_type.strip().lower()
    if mt not in {"movie", "series"}:
        return error_response(tool, code="INVALID_ARGUMENT", message='media_type must be "movie" or "series"')
    if library_id is None and not (query and query.strip()):
        return error_response(tool, code="INVALID_ARGUMENT", message="library_id or query is required")
    if scope and scope not in {"movie", "series", "season", "episode"}:
        return error_response(tool, code="INVALID_ARGUMENT", message="scope must be movie, series, season, or episode")
    if mt == "movie" and scope not in {None, "movie"}:
        return error_response(tool, code="INVALID_ARGUMENT", message="movie scope must be movie")
    if mt == "series" and scope == "movie":
        return error_response(tool, code="INVALID_ARGUMENT", message="series scope cannot be movie")
    if mt == "movie" and (season is not None or episode is not None or scope in {"series", "season", "episode"}):
        return error_response(tool, code="INVALID_ARGUMENT", message="movie staging does not accept season or episode scope")
    try:
        if mt == "movie":
            base_url, api_key = settings.radarr_url, settings.radarr_api_key
        else:
            base_url, api_key = settings.sonarr_url, settings.sonarr_api_key
        with ArrClient(base_url, api_key, timeout_seconds=60) as arr:
            payload = worker_arr_status(arr, media_type=mt, library_id=library_id, query=query, season=season)
        if payload.get("ok") is not True:
            return error_response(tool, code="UPSTREAM_ERROR", message="Arr could not resolve the requested title")
        files = [dict(row) for row in payload.get("files", []) if row.get("path")]
        if not files:
            return error_response(tool, code="NOT_FOUND", message="No completed media files were found for the title")
        selected_scope = scope or ("movie" if mt == "movie" else ("episode" if episode is not None else "season"))
        if mt == "series":
            if selected_scope == "season":
                chosen_season = season if season is not None else min(int(row.get("season") or 0) for row in files)
                files = [row for row in files if int(row.get("season") or -1) == chosen_season]
                selection = f"s{chosen_season}"
            elif selected_scope == "episode":
                if season is None or episode is None:
                    return error_response(tool, code="INVALID_ARGUMENT", message="episode scope requires season and episode")
                files = [row for row in files if int(row.get("season") or -1) == season and int(row.get("episode") or -1) == episode]
                selection = f"s{season}e{episode}"
            else:
                selection = "series"
        else:
            selection = "movie"
        if not files:
            return error_response(tool, code="NOT_FOUND", message="Requested scope has no completed files")
        paths = sorted({str(row["path"]) for row in files})
        if len(paths) != len(files):
            return error_response(tool, code="COLLISION", message="Requested scope contains duplicate media paths")
        with _qbt_client(settings) as client:
            torrents = client.torrents_info_raw()
        owners = []
        for torrent in torrents:
            root = str(torrent.get("content_path") or torrent.get("save_path") or "").rstrip("/")
            torrent_hash = str(torrent.get("hash") or "")
            try:
                torrent_files = client.torrent_files(torrent_hash) if torrent_hash else []
            except Exception:
                torrent_files = []
            owned = []
            for path in paths:
                if path == root:
                    owned.append(path)
                    continue
                relative = path[len(root) + 1 :] if root and path.startswith(root + "/") else ""
                if relative and any(str(row.get("name") or "").lstrip("/") == relative for row in torrent_files):
                    owned.append(path)
            if owned:
                owners.append({"hash": torrent_hash, "name": torrent.get("name"), "content_path": root, "progress": torrent.get("progress"), "state": torrent.get("state"), "category": torrent.get("category"), "tags": torrent.get("tags"), "paths": owned, "size": int(torrent.get("size") or 0)})
        blockers: list[str] = []
        if not owners:
            blockers.append("qbt_ownership_missing")
        if len({row["hash"] for row in owners}) != len(owners):
            blockers.append("qbt_ownership_ambiguous")
        if any(float(row.get("progress") or 0) < 0.999999 for row in owners):
            blockers.append("incomplete")
        if any(_media_stage_owner_excluded(settings, row) for row in owners):
            blockers.append("excluded_torrent_label")
        hdd_root = Path(settings.media_hdd_mount).resolve()
        if any(not Path(path).resolve().is_relative_to(hdd_root) for path in paths):
            blockers.append("unsupported_source_path")
        total_size = sum(int(row.get("size") or 0) for row in files)
        free_bytes = shutil.disk_usage(settings.media_nvme_mount).free
        if free_bytes < total_size:
            blockers.append("insufficient_nvme_space")
        destinations = [str(Path(settings.media_nvme_mount) / Path(path).resolve().relative_to(hdd_root)) if Path(path).resolve().is_relative_to(hdd_root) else str(Path(settings.media_nvme_mount) / Path(path).name) for path in paths]
        if any(Path(destination).exists() for destination in destinations):
            blockers.append("destination_collision")
        records = sorted((str(row["path"]), int(row.get("size") or 0)) for row in files)
        digest_input = json.dumps(records, sort_keys=True).encode()
        plan_digest = hashlib.sha256(digest_input).hexdigest()[:16]
        expected_action = canonical_media_title_to_nvme(mt, int(library_id or payload.get("library_id") or 0), selected_scope, selection, plan_digest)
        data = {"media_type": mt, "library_id": library_id or payload.get("library_id"), "title": payload.get("title"), "scope": selected_scope, "selection": selection, "files": files, "paths": paths, "destinations": destinations, "total_size": total_size, "nvme_free_bytes": free_bytes, "qbt_torrents": owners, "blockers": blockers, "warnings": [], "ready": not blockers, "expected_action": expected_action, "mutation": False}
        return ok_response(tool, data, duration_ms=int((time.perf_counter() - started) * 1000))
    except Exception as exc:
        return _qbt_upstream_error(tool, exc)


def media_title_to_nvme_status(settings: Settings, **kwargs: Any) -> str:
    """Status is the same read-only resolution with current filesystem/qBT state."""
    result = media_title_to_nvme_plan(settings, **kwargs)
    if json.loads(result).get("ok") is False:
        return result
    body = json.loads(result)
    body["tool"] = TOOL_MEDIA_TITLE_TO_NVME_STATUS
    body["data"]["status_only"] = True
    body["data"]["staged"] = all(str(path).startswith(settings.media_nvme_mount.rstrip("/") + "/") for path in body["data"].get("paths", []))
    return json.dumps(body, separators=(",", ":"))
def qbt_torrent_force_start(
    settings: Settings,
    *,
    confirm: bool = False,
    torrent_hash: str | None = None,
    name_contains: str | None = None,
) -> str:
    started = time.perf_counter()
    tool = TOOL_TORRENT_FORCE_START
    tier = 2
    disabled = _check_mutations_enabled(settings, tool)
    if disabled:
        return disabled

    needle_hash = (torrent_hash or "").strip().lower()
    needle_name = (name_contains or "").strip()
    if not needle_hash and not needle_name:
        return error_response(
            tool,
            code="INVALID_ARGUMENT",
            message="Pass torrent_hash or name_contains.",
        )
    if needle_hash and not _HEX_RE.match(needle_hash):
        return error_response(
            tool,
            code="INVALID_ARGUMENT",
            message="torrent_hash must be a 40 or 64 hex character string",
        )

    gate_err = require_tier2(confirm=confirm)
    if gate_err:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=False,
            duration_ms=duration_ms,
            error_code=gate_err["code"],
        )
        return error_response(
            tool,
            code=gate_err["code"],
            message=gate_err["message"],
            hint=gate_err.get("hint", ""),
        )

    try:
        with _qbt_client(settings) as client:
            torrents = client.torrents_info_raw()
            if needle_hash:
                matches = [
                    t
                    for t in torrents
                    if str(t.get("hash") or "").lower() == needle_hash
                ]
            else:
                folded = needle_name.lower()
                matches = [
                    t
                    for t in torrents
                    if folded in str(t.get("name") or "").lower()
                ]

            if not matches:
                return error_response(
                    tool,
                    code="NOT_FOUND",
                    message="No matching torrent in qBittorrent.",
                )
            if len(matches) > 1:
                samples = [
                    {"hash": str(t.get("hash") or ""), "name": str(t.get("name") or "")}
                    for t in matches[:8]
                ]
                return error_response(
                    tool,
                    code="AMBIGUOUS_TORRENT",
                    message="Multiple torrents match; pass torrent_hash.",
                    hint=str(samples),
                )

            row = matches[0]
            target = str(row.get("hash") or "").lower()
            before_state = str(row.get("state") or "")
            client.torrents_set_priority([target], "top")
            client.torrents_resume(hashes=[target])
            # qBittorrent 5 clears force_start on torrents/start — set after resume.
            client.torrents_set_force_start([target], True)
            after_rows = [
                t
                for t in client.torrents_info_raw()
                if str(t.get("hash") or "").lower() == target
            ]
            after = after_rows[0] if after_rows else row

        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=True,
            duration_ms=duration_ms,
            detail={"hash": target, "before_state": before_state},
        )
        return ok_response(
            tool,
            {
                "mutation": True,
                "action": "force_start",
                "target": target,
                "applied": {"force_start": True, "target": target},
                "hash": target,
                "name": str(row.get("name") or ""),
                "before_state": before_state,
                "after_state": str(after.get("state") or ""),
                "dlspeed": after.get("dlspeed"),
                "progress": after.get("progress"),
            },
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return _qbt_upstream_error(tool, exc)


def qbt_exclusive_status(settings: Settings) -> str:
    started = time.perf_counter()
    tool = TOOL_EXCLUSIVE_STATUS
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(f"{settings.qbt_events_url.rstrip('/')}/v1/exclusive/status")
        if response.status_code >= 400:
            return _events_http_error(tool, response)
        payload = response.json()
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ok_response(tool, payload, duration_ms=duration_ms)
    except Exception as exc:
        return _events_upstream_error(tool, exc)


def qbt_exclusive_prioritize(
    settings: Settings,
    *,
    name_contains: str,
    confirm: bool = False,
) -> str:
    started = time.perf_counter()
    tool = TOOL_EXCLUSIVE_PRIORITIZE
    tier = 2
    disabled = _check_mutations_enabled(settings, tool)
    if disabled:
        return disabled

    needle = name_contains.strip()
    if not needle:
        return error_response(
            tool,
            code="INVALID_ARGUMENT",
            message="name_contains must not be empty",
        )

    gate_err = require_tier2(confirm=confirm)
    if gate_err:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=False,
            duration_ms=duration_ms,
            error_code=gate_err["code"],
        )
        return error_response(
            tool,
            code=gate_err["code"],
            message=gate_err["message"],
            hint=gate_err.get("hint", ""),
        )

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{settings.qbt_events_url.rstrip('/')}/v1/exclusive/start",
                json={"name_contains": needle, "confirm": True},
            )
        if response.status_code >= 400:
            duration_ms = int((time.perf_counter() - started) * 1000)
            try:
                err_body = response.json()
                error_code = err_body.get("error")
            except Exception:
                error_code = None
            write_audit_record(
                audit_path=settings.audit_path,
                tool=tool,
                tier=tier,
                ok=False,
                duration_ms=duration_ms,
                error_code=str(error_code or response.status_code),
            )
            return _events_http_error(tool, response)

        payload = response.json()
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=True,
            duration_ms=duration_ms,
            detail={
                "name_contains": needle,
                "session_id": payload.get("session_id"),
                "priority_count": payload.get("priority_count"),
                "paused_count": payload.get("paused_count"),
            },
        )
        return ok_response(
            tool,
            {"mutation": True, **payload},
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return _events_upstream_error(tool, exc)


def qbt_exclusive_cancel(
    settings: Settings,
    *,
    confirm: bool = False,
) -> str:
    started = time.perf_counter()
    tool = TOOL_EXCLUSIVE_CANCEL
    tier = 2
    disabled = _check_mutations_enabled(settings, tool)
    if disabled:
        return disabled

    gate_err = require_tier2(confirm=confirm)
    if gate_err:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=False,
            duration_ms=duration_ms,
            error_code=gate_err["code"],
        )
        return error_response(
            tool,
            code=gate_err["code"],
            message=gate_err["message"],
            hint=gate_err.get("hint", ""),
        )

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{settings.qbt_events_url.rstrip('/')}/v1/exclusive/cancel",
                json={"confirm": True},
            )
        if response.status_code >= 400:
            duration_ms = int((time.perf_counter() - started) * 1000)
            try:
                err_body = response.json()
                error_code = err_body.get("error")
            except Exception:
                error_code = None
            write_audit_record(
                audit_path=settings.audit_path,
                tool=tool,
                tier=tier,
                ok=False,
                duration_ms=duration_ms,
                error_code=str(error_code or response.status_code),
            )
            return _events_http_error(tool, response)

        payload = response.json()
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=True,
            duration_ms=duration_ms,
            detail={
                "session_id": payload.get("session_id"),
                "resumed_count": payload.get("resumed_count"),
            },
        )
        return ok_response(
            tool,
            {"mutation": True, **payload},
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        write_audit_record(
            audit_path=settings.audit_path,
            tool=tool,
            tier=tier,
            ok=False,
            duration_ms=duration_ms,
            error_code="UPSTREAM_ERROR",
        )
        return _events_upstream_error(tool, exc)
