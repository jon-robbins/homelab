from __future__ import annotations

from homelab_mcp.mutation_gate import (
    canonical_arr_ensure_movie,
    canonical_arr_ensure_series,
    canonical_grab_prowlarr,
    canonical_grab_radarr,
    canonical_grab_sonarr,
    canonical_offload,
    canonical_pause_hashes,
    canonical_pause_tag,
    canonical_profile,
    canonical_resume_tag,
    canonical_season_ensure,
    require_tier1,
    require_tier2,
)


def test_tier2_rejects_without_confirm() -> None:
    err = require_tier2(confirm=False)
    assert err is not None
    assert err["code"] == "CONFIRMATION_REQUIRED"


def test_tier2_accepts_confirm() -> None:
    assert require_tier2(confirm=True) is None


def test_tier1_rejects_mismatch() -> None:
    err = require_tier1(expected="profile:sample", provided="profile:other")
    assert err is not None
    assert err["code"] == "EXPECTED_ACTION_MISMATCH"
    assert "profile:sample" in err["hint"]


def test_tier1_accepts_match() -> None:
    assert require_tier1(expected="profile:sample", provided="profile:sample") is None


def test_canonical_season_ensure() -> None:
    assert canonical_season_ensure(226, 11) == "sonarr-season-ensure:226:11"


def test_canonical_strings() -> None:
    assert canonical_resume_tag("sample") == "resume:tag:sample"
    assert canonical_profile("sample") == "profile:sample"
    assert canonical_pause_tag("sample") == "pause:tag:sample"
    assert canonical_pause_hashes(3) == "pause:hashes:3"
    assert canonical_offload(2) == "offload:2"
    assert canonical_grab_radarr(42, 1000) == "grab:radarr:42:1000"
    assert canonical_grab_sonarr(99, 500) == "grab:sonarr:99:500"
    assert canonical_grab_prowlarr(7, 100) == "grab:prowlarr:7:100"


def test_canonical_arr_ensure_strings() -> None:
    assert canonical_arr_ensure_movie(1306400) == "ensure:movie:1306400"
    assert canonical_arr_ensure_series(100, 2) == "ensure:series:100:s2"
