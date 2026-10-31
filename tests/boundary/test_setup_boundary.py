"""0018 decision 9: `setup/` may call the platform; the platform never calls `setup/`. Deleting it must not matter."""

import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PLATFORM_DIRS = ("scripts", "host", "modules")
TEXT_REFERENCE = re.compile(r"(?:^|[\s\"'=/(])setup/|-m\s+setup\b|\bsetup\.(?:engine|cli|tui|providers|wire)\b")

# What setup/ may take from the platform. Everything signed-release related (feed, admission, materialize,
# reconcile, homelabctl) is deliberately missing: local archives must never reach those paths.
ALLOWED_PLATFORM_IMPORTS = {
    "scripts.controller.planner",
    "scripts.controller.state",
    "scripts.controller.reconcile",  # SubprocessOps only; see test below
    "scripts.release.render",
    "scripts.release.modules",
    "scripts.release.compose_policy",
    "scripts.release.contracts",
}


def imported_modules(path: Path) -> list[tuple[str, list[str]]]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [(alias.name, []) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            found.append((node.module or "", [alias.name for alias in node.names]))
    return found


def reverse_dependencies(root: Path) -> list[str]:
    """Files under scripts/, host/ and modules/ that import or invoke setup/."""
    hits = []
    for name in PLATFORM_DIRS:
        for path in sorted((root / name).rglob("*")):
            if not path.is_file() or path.suffix in {".pyc", ".png", ".jpg"}:
                continue
            relative = path.relative_to(root)
            if path.suffix == ".py" and any(m == "setup" or m.startswith("setup.") for m, _ in imported_modules(path)):
                hits.append(f"{relative}: imports setup")
            elif TEXT_REFERENCE.search(path.read_text(encoding="utf-8", errors="ignore")):
                hits.append(f"{relative}: refers to setup/")
    return hits


def forbidden_platform_imports(root: Path) -> list[str]:
    hits = []
    for path in sorted((root / "setup").rglob("*.py")):
        if "tests" in path.relative_to(root).parts:
            continue
        for module, names in imported_modules(path):
            if module.startswith("scripts") and module not in ALLOWED_PLATFORM_IMPORTS:
                hits.append(f"{path.relative_to(root)}: imports {module}")
            if module == "scripts.controller.reconcile" and names != ["SubprocessOps"]:
                hits.append(f"{path.relative_to(root)}: takes {names} from reconcile")
    return hits


def test_nothing_in_the_platform_depends_on_setup():
    assert reverse_dependencies(ROOT) == []


def test_the_scan_catches_a_reverse_import_and_a_script_that_calls_setup(tmp_path):
    for name in PLATFORM_DIRS:
        (tmp_path / name).mkdir()
    (tmp_path / "scripts/ok.py").write_text("import json\nfrom scripts.release import render\n")
    assert reverse_dependencies(tmp_path) == []
    (tmp_path / "scripts/bad.py").write_text("from setup.engine import runner\n")
    (tmp_path / "host/bad.sh").write_text("python3 -m setup.cli apply\n")
    (tmp_path / "modules/bad.yml").write_text("command: setup/engine/run\n")
    assert len(reverse_dependencies(tmp_path)) == 3


def test_setup_never_reaches_the_signed_release_path():
    assert forbidden_platform_imports(ROOT) == []


def test_the_forward_scan_catches_materialize_feed_and_reconcile(tmp_path):
    (tmp_path / "setup").mkdir()
    (tmp_path / "setup/bad.py").write_text(
        "from scripts.controller.materialize import materialize\n"
        "from scripts.controller.reconcile import reconcile\n"
        "import scripts.controller.feed\n"
    )
    assert len(forbidden_platform_imports(tmp_path)) == 3


def test_owner_pipeline_tests_pass_with_setup_absent(tmp_path):
    copy = tmp_path / "homelab"

    def ignore(directory, names):
        top = {".git", ".nox", ".worktrees", "setup"} if Path(directory) == ROOT else set()
        return top | {n for n in names if n in {"__pycache__", ".pytest_cache"}}

    shutil.copytree(ROOT, copy, ignore=ignore, symlinks=True)
    assert not (copy / "setup").exists()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test"]  # the docs link test lists tracked files
    for argv in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "copy"]):
        subprocess.run([*git, *argv], cwd=copy, check=True, capture_output=True)
    env = {**os.environ, "PYTHONPATH": str(copy), "PYTHONDONTWRITEBYTECODE": "1"}
    budget = subprocess.run([sys.executable, "scripts/ci/complexity_budget.py"], cwd=copy, env=env, capture_output=True, text=True)
    assert budget.returncode == 0, budget.stdout + budget.stderr
    imports = "import scripts.controller.homelabctl, scripts.release.render, scripts.release.reconcile"
    loaded = subprocess.run([sys.executable, "-c", imports], cwd=copy, env=env, capture_output=True, text=True)
    assert loaded.returncode == 0, loaded.stderr
    tests = ["tests/controller", "tests/ci/test_complexity_budget.py", "tests/docs"]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        cwd=copy, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-1000:]
