from __future__ import annotations

from typing import Any

from homelab_workers.shared.seerr_reconciler_models import (
    DiagnosisResult,
    NormalizedSeerrRequest,
    ReleaseEvidence,
)
from homelab_workers.shared.seerr_reconciler_policy import (
    classify_rejections,
    fallback_allowed,
)
from homelab_workers.shared.seerr_reconciler_state import failed_guids_for


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _bool(value: object) -> bool:
    return bool(value)


def shape_release(row: dict[str, Any], search_mode: str = "title") -> ReleaseEvidence:
    rejections = row.get("rejections")
    return ReleaseEvidence(
        title=str(row.get("title") or row.get("release_title") or ""),
        guid=str(row.get("guid") or ""),
        indexer_id=_int(row.get("indexer_id") or row.get("indexerId")),
        size_bytes=_int(row.get("size_bytes") or row.get("size")),
        seeders=_int(row.get("seeders")),
        leechers=_int(row.get("leechers")),
        quality=str(row.get("quality") or "") or None,
        rejections=(
            tuple(str(item) for item in rejections)
            if isinstance(rejections, list)
            else ()
        ),
        download_allowed=_bool(row.get("download_allowed") or row.get("downloadAllowed")),
        search_mode=search_mode,
        raw=dict(row),
    )


def _state_row(state: dict[str, Any], request_key: str) -> dict[str, Any]:
    requests = state.get("requests")
    if not isinstance(requests, dict):
        return {}
    row = requests.get(request_key)
    return row if isinstance(row, dict) else {}


def _qbt_by_hash(qbt_torrents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for torrent in qbt_torrents:
        torrent_hash = str(torrent.get("hash") or torrent.get("torrent_hash") or "").lower()
        if torrent_hash:
            out[torrent_hash] = torrent
    return out


def _failed_hashes(row: dict[str, Any]) -> set[str]:
    failed_hashes = row.get("failed_hashes")
    if not isinstance(failed_hashes, list):
        return set()
    return {
        str(torrent_hash).strip().lower()
        for torrent_hash in failed_hashes
        if str(torrent_hash).strip()
    }


def _arr_fallback_reason(arr_options: list[ReleaseEvidence]) -> str | None:
    rejection_classes = {
        rejection_class
        for option in arr_options
        for rejection_class in classify_rejections(option.rejections)
    }
    if not rejection_classes:
        return "no Arr soft rejection evidence"
    if any(rejection_class.startswith("hard_") for rejection_class in rejection_classes):
        return "Arr hard rejection"
    if not rejection_classes.issubset({"soft_size", "soft_quality"}):
        return "Arr rejection is not a soft override"
    return None


def diagnose_request(
    request: NormalizedSeerrRequest,
    *,
    arr_status: dict[str, Any],
    arr_releases: list[dict[str, Any]],
    prowlarr_releases: list[dict[str, Any]],
    qbt_torrents: list[dict[str, Any]],
    state: dict[str, Any],
) -> DiagnosisResult:
    row = _state_row(state, request.state_key)
    qbt_hash = str(row.get("hash") or "").lower()
    qbt_by_hash = _qbt_by_hash(qbt_torrents)
    if qbt_hash and qbt_hash in qbt_by_hash:
        torrent = qbt_by_hash[qbt_hash]
        progress = float(torrent.get("progress") or 0.0)
        if progress >= 0.999:
            if arr_status.get("has_file") is True:
                return DiagnosisResult(
                    request,
                    "imported",
                    "qBittorrent torrent completed; Arr reports file available",
                    {"torrent": torrent, "arr_status": arr_status},
                )
            return DiagnosisResult(
                request,
                "downloading",
                "qBittorrent torrent completed; waiting for Arr import",
                {"torrent": torrent},
            )
        return DiagnosisResult(
            request,
            "downloading",
            "qBittorrent torrent is active",
            {"torrent": torrent},
        )

    if arr_status.get("has_file") is True:
        return DiagnosisResult(
            request,
            "available",
            "Arr reports file available",
            {"arr_status": arr_status},
        )
    if arr_status.get("in_library") is not True:
        return DiagnosisResult(
            request,
            "waiting_for_arr",
            "request is not in Arr library",
            {"arr_status": arr_status},
        )

    arr_options = [shape_release(release, "arr") for release in arr_releases]
    downloadable = [option for option in arr_options if option.download_allowed]
    if downloadable:
        selected = downloadable[0]
        return DiagnosisResult(
            request,
            "arr_monitored",
            "Arr has a downloadable release",
            {"arr_status": arr_status},
            tuple(downloadable),
            "grab_arr_release",
            f"grab:arr:{selected.guid}",
        )

    fallback_options = [shape_release(release, "title") for release in prowlarr_releases]
    arr_fallback_reason = _arr_fallback_reason(arr_options)
    if arr_fallback_reason is not None:
        state_name = (
            "blocked_no_candidate"
            if arr_fallback_reason == "no Arr soft rejection evidence"
            else "blocked_hard_rejection"
        )
        return DiagnosisResult(
            request,
            state_name,
            arr_fallback_reason,
            {"arr_status": arr_status, "arr_rejections": [option.raw for option in arr_options]},
            tuple(fallback_options),
            "manual_review",
        )

    allowed, reason, selected = fallback_allowed(
        request,
        fallback_options,
        failed_guids_for(state, request.state_key),
        _failed_hashes(row),
    )
    if allowed and selected is not None:
        return DiagnosisResult(
            request,
            "fallback_candidate_found",
            reason,
            {"arr_status": arr_status, "arr_rejections": [option.raw for option in arr_options]},
            tuple(fallback_options),
            "grab_fallback",
            f"grab:prowlarr:{selected.indexer_id}:{selected.guid}",
        )
    state_name = "blocked_no_candidate" if not fallback_options else "blocked_hard_rejection"
    if reason == "candidate has no seeders":
        state_name = "blocked_no_seeders"
    if reason == "stable external id required" or "identity" in reason:
        state_name = "blocked_identity_ambiguous"
    return DiagnosisResult(
        request,
        state_name,  # type: ignore[arg-type]
        reason,
        {"arr_status": arr_status, "arr_rejections": [option.raw for option in arr_options]},
        tuple(fallback_options),
        "manual_review",
    )
