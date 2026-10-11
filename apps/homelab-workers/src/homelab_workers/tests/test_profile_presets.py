from homelab_workers.shared.profile_presets import PROFILE_NAMES, get_profile_preferences


def test_profile_names() -> None:
    assert "max-downloads" in PROFILE_NAMES


def test_max_downloads_preset() -> None:
    prefs = get_profile_preferences("max-downloads")
    assert prefs["queueing_enabled"] is False
    assert prefs["max_active_downloads"] == 100
    assert prefs["max_connec_per_torrent"] == 100
    assert prefs["dl_limit"] == 0


def test_unknown_profile_raises() -> None:
    try:
        get_profile_preferences("custom")
        raised = False
    except ValueError as exc:
        raised = True
        assert "unknown profile" in str(exc)
    assert raised
