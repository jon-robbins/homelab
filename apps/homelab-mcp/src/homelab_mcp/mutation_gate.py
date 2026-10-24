from __future__ import annotations

from enum import Enum


class GateTier(Enum):
    TIER1_DESTRUCTIVE = 1
    TIER2_RECOVERABLE = 2


def require_tier2(*, confirm: bool) -> dict[str, str] | None:
    if confirm is not True:
        return {
            "code": "CONFIRMATION_REQUIRED",
            "message": "Mutation requires confirm=true",
            "hint": "Pass confirm=true after reviewing the action.",
        }
    return None


def require_tier1(*, expected: str, provided: str | None) -> dict[str, str] | None:
    if provided != expected:
        return {
            "code": "EXPECTED_ACTION_MISMATCH",
            "message": "expected_action does not match the computed action",
            "hint": f"Pass expected_action={expected!r}",
        }
    return None


def canonical_resume_tag(tag: str) -> str:
    return f"resume:tag:{tag.strip()}"


def canonical_resume_hashes(count: int) -> str:
    return f"resume:hashes:{count}"


def canonical_profile(profile: str) -> str:
    return f"profile:{profile.strip()}"


def canonical_pause_tag(tag: str) -> str:
    return f"pause:tag:{tag.strip()}"


def canonical_pause_hashes(count: int) -> str:
    return f"pause:hashes:{count}"


def canonical_offload(limit: int) -> str:
    return f"offload:{limit}"


def canonical_media_title_to_nvme(
    media_type: str, library_id: int, scope: str, selection: str, plan_digest: str
) -> str:
    return f"media-title-to-nvme:{media_type}:{library_id}:{scope}:{selection}:{plan_digest}"


def canonical_grab_radarr(movie_id: int, size_bytes: int) -> str:
    return f"grab:radarr:{movie_id}:{size_bytes}"


def canonical_grab_sonarr(episode_id: int, size_bytes: int) -> str:
    return f"grab:sonarr:{episode_id}:{size_bytes}"


def canonical_grab_prowlarr(indexer_id: int, size_bytes: int) -> str:
    return f"grab:prowlarr:{indexer_id}:{size_bytes}"


def canonical_season_ensure(series_id: int, season_number: int) -> str:
    return f"sonarr-season-ensure:{series_id}:{season_number}"


def canonical_arr_ensure_movie(tmdb_id: int) -> str:
    return f"ensure:movie:{tmdb_id}"


def canonical_arr_ensure_series(stable_id: int, season: int) -> str:
    return f"ensure:series:{stable_id}:s{season}"


def canonical_file_move(src: str, dest: str) -> str:
    return f"file-move:{src}->{dest}"


def canonical_file_delete(path: str) -> str:
    return f"file-delete:{path}"


def canonical_trash_empty(older_than_days: int) -> str:
    return f"trash-empty:{older_than_days}"


def canonical_qbt_delete(hashes: list[str], *, delete_files: bool) -> str:
    mode = "files" if delete_files else "keep-files"
    return f"qbt-delete:{mode}:{','.join(hashes)}"


def canonical_arr_delete(media_type: str, library_id: int, *, delete_files: bool) -> str:
    mode = "files" if delete_files else "keep-files"
    return f"arr-delete:{media_type}:{library_id}:{mode}"


def canonical_filter_disable(filter_name: str) -> str:
    return f"filter:disable:{filter_name.strip()}"


def canonical_filter_enable(filter_name: str) -> str:
    return f"filter:enable:{filter_name.strip()}"


def canonical_irc_restart(network_name: str) -> str:
    return f"irc:restart:{network_name.strip()}"
