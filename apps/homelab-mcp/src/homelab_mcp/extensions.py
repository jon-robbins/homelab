"""Opt-in discovery and registration for third-party MCP tool extensions."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
import os
from typing import Any, Callable


EXTENSION_ENTRY_POINT_GROUP = "homelab_mcp.extensions"
EXTENSIONS_ENV = "HOMELAB_MCP_EXTENSIONS"


@dataclass(frozen=True)
class ExtensionContext:
    """Stable values supplied to an extension registration hook."""

    mcp: Any
    settings: Any
    register_tool: Callable[..., Any]
    capability_descriptors: list[dict[str, Any]] | None = None
    release_grab_guards: list[Callable[[int | None, str | None], str | None]] | None = None
    tool_roles: dict[str, tuple[str, str]] | None = None
    qbt_profiles: dict[str, dict[str, Any]] | None = None
    # Each check returns {"name": str, "health": dict, "causes": [[code, recommendation], ...]}
    # and is folded into autobrr_pipeline_diagnosis.
    autobrr_pipeline_checks: list[Callable[[], dict[str, Any]]] | None = None


def configured_extension_names(value: str | None = None) -> tuple[str, ...]:
    """Return the explicitly enabled entry-point names in declaration order."""
    raw = os.environ.get(EXTENSIONS_ENV, "") if value is None else value
    names = tuple(part.strip() for part in raw.split(",") if part.strip())
    if len(names) != len(set(names)):
        raise RuntimeError(f"{EXTENSIONS_ENV} contains duplicate extension names")
    return names


def _entry_points() -> tuple[Any, ...]:
    discovered = metadata.entry_points()
    if hasattr(discovered, "select"):
        return tuple(discovered.select(group=EXTENSION_ENTRY_POINT_GROUP))
    return tuple(discovered.get(EXTENSION_ENTRY_POINT_GROUP, ()))


def load_configured_extensions(
    context: ExtensionContext,
    *,
    names: tuple[str, ...] | None = None,
    entry_points: tuple[Any, ...] | None = None,
) -> tuple[str, ...]:
    """Load only explicitly enabled extensions and invoke their hooks once."""
    enabled = configured_extension_names() if names is None else names
    if not enabled:
        return ()

    available: dict[str, Any] = {}
    for entry_point in _entry_points() if entry_points is None else entry_points:
        if entry_point.name in available:
            raise RuntimeError(
                f"duplicate {EXTENSION_ENTRY_POINT_GROUP} entry point: {entry_point.name}"
            )
        available[entry_point.name] = entry_point

    missing = [name for name in enabled if name not in available]
    if missing:
        raise RuntimeError(
            "enabled homelab-mcp extension(s) are not installed: " + ", ".join(missing)
        )

    for name in enabled:
        hook = available[name].load()
        if not callable(hook):
            raise RuntimeError(f"homelab-mcp extension {name!r} is not callable")
        hook(context)
    return enabled
