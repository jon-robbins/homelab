from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from homelab_mcp.extensions import (
    ExtensionContext,
    configured_extension_names,
    load_configured_extensions,
)


class FakeEntryPoint:
    def __init__(self, name, hook):
        self.name = name
        self._hook = hook

    def load(self):
        return self._hook


def _context() -> ExtensionContext:
    return ExtensionContext(
        mcp=SimpleNamespace(), settings=SimpleNamespace(), register_tool=lambda *a, **k: None
    )


def test_extensions_are_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("HOMELAB_MCP_EXTENSIONS", raising=False)
    assert configured_extension_names() == ()
    assert load_configured_extensions(_context(), entry_points=()) == ()


def test_only_explicitly_enabled_extension_loads() -> None:
    calls = []
    entries = (
        FakeEntryPoint("example", lambda context: calls.append(("example", context))),
        FakeEntryPoint("other", lambda context: calls.append(("other", context))),
    )
    context = _context()
    assert load_configured_extensions(
        context, names=("example",), entry_points=entries
    ) == ("example",)
    assert calls == [("example", context)]


def test_missing_enabled_extension_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="not installed: example"):
        load_configured_extensions(_context(), names=("example",), entry_points=())


def test_duplicate_configured_names_are_rejected() -> None:
    with pytest.raises(RuntimeError, match="duplicate extension names"):
        configured_extension_names("example,example")


def test_public_server_starts_without_any_extension_package() -> None:
    retired_extension = "tracker" + "_integrations"
    code = """
import importlib.abc
import sys

class BlockExtensions(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {RETired_EXTENSION, 'example_extension'}:
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockExtensions())
from homelab_mcp.server import mcp
names = set(mcp._tool_manager._tools)
private = {'example_private_status', 'example_private_plan'}
assert names
assert names.isdisjoint(private)
"""
    code = code.replace("RETired_EXTENSION", repr(retired_extension))
    env = os.environ.copy()
    env.pop("HOMELAB_MCP_EXTENSIONS", None)
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[5],
        env=env,
        check=True,
    )
