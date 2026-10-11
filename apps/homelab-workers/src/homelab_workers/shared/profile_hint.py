from __future__ import annotations

from typing import Any, Mapping

from homelab_workers.shared.profile_presets import PROFILE_NAMES, get_profile_preferences


def _matches_profile(prefs: dict[str, Any], template: dict[str, Any]) -> bool:
    for key, want in template.items():
        if prefs.get(key) != want:
            return False
    return True


def infer_qbt_profile_hint(
    prefs: dict[str, Any], extension_profiles: Mapping[str, Mapping[str, Any]] | None = None
) -> str:
    profiles = {name: get_profile_preferences(name) for name in PROFILE_NAMES}
    profiles.update(extension_profiles or {})
    for name, template in sorted(profiles.items()):
        if _matches_profile(prefs, template):
            return name
    return "custom"
