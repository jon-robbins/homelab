from __future__ import annotations

import ast
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TARGET_MODULES = (
    PACKAGE_ROOT / "shared" / "qbt_offload.py",
    PACKAGE_ROOT / "cli" / "qbt_offload.py",
    PACKAGE_ROOT / "cli" / "arr_missing_search.py",
    PACKAGE_ROOT / "cli" / "seerr_reconciler.py",
    PACKAGE_ROOT / "cli" / "qbt_events.py",
)
GENERIC_QBT_OFFLOAD_MODULES = TARGET_MODULES[:2]
PRIVATE_IMPORT_PREFIXES = ("tl_", "seed_group_")
PRIVATE_POLICY_LITERALS = (
    "seed-group",
    "fictional-tracker",
    "example-intake-accepted",
    "seed-staging",
    "/seedstore",
    "seedstore",
)


def _imported_modules(source_path: Path) -> list[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


@pytest.mark.parametrize("source_path", TARGET_MODULES, ids=lambda path: path.stem)
def test_retained_worker_modules_do_not_import_private_tracker_modules(
    source_path: Path,
) -> None:
    private_imports = [
        module
        for module in _imported_modules(source_path)
        if any(
            component.startswith(PRIVATE_IMPORT_PREFIXES)
            for component in module.split(".")
        )
    ]

    assert private_imports == []


@pytest.mark.parametrize(
    "source_path",
    GENERIC_QBT_OFFLOAD_MODULES,
    ids=lambda path: f"{path.parent.name}.{path.stem}",
)
def test_generic_qbt_offload_sources_do_not_embed_private_policy_literals(
    source_path: Path,
) -> None:
    source = source_path.read_text(encoding="utf-8").casefold()
    present_literals = [literal for literal in PRIVATE_POLICY_LITERALS if literal in source]

    assert present_literals == []
