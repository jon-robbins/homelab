from __future__ import annotations

from typing import Any

PROFILE_NAMES = frozenset({"max-downloads"})

_PRESETS: dict[str, dict[str, Any]] = {
    "max-downloads": {
        "queueing_enabled": False,
        "max_active_downloads": 100,
        "max_active_uploads": -1,
        "max_connec": -1,
        "max_connec_per_torrent": 100,
        "dl_limit": 0,
        "up_limit": 0,
    },
}


def get_profile_preferences(name: str) -> dict[str, object]:
    key = name.strip()
    if key not in _PRESETS:
        raise ValueError(f"unknown profile: {key}")
    return dict(_PRESETS[key])
