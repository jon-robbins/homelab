from __future__ import annotations

DEFAULT_ALLOWED_PREFIXES = (
    "homelab_",
    "qbt_",
    "sonarr_",
    "radarr_",
    "prowlarr_",
    "bazarr_",
    "autobrr_",
)


def tool_is_allowed(tool_name: str, allowlist: list[str] | None = None) -> bool:
    if allowlist:
        return tool_name in allowlist
    return tool_name.startswith(DEFAULT_ALLOWED_PREFIXES)


def filter_tool_specs(
    tool_specs: list[dict[str, object]], allowlist: list[str] | None = None
) -> list[dict[str, object]]:
    return [spec for spec in tool_specs if tool_is_allowed(str(spec.get("name", "")), allowlist)]
