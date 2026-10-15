"""Check configured non-test source line budgets."""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/ci/budgets.json"
def source_files(root: Path, path: str):
    target = root / path
    if target.is_file():
        candidates = [target]
    else:
        candidates = target.rglob("*")
    return (
        file
        for file in candidates
        if file.is_file()
        and file.suffix in {".py", ".sh"}
        and "tests" not in file.relative_to(root).parts
        and not (file.suffix == ".py" and (file.name.startswith("test_") or file.name.endswith("_test.py")))
    )


def source_lines(path: Path) -> int:
    return sum(
        bool(line.strip()) and not line.lstrip().startswith("#")
        for line in path.read_text(encoding="utf-8").splitlines()
    )


def violations(root: Path, config: dict) -> list[str]:
    failures = []
    for budget in config["budgets"]:
        count = sum(source_lines(file) for path in budget["paths"] for file in source_files(root, path))
        if count > budget["max_lines"]:
            failures.append(f"{', '.join(budget['paths'])}: {count} > {budget['max_lines']}")
    return failures


def main() -> int:
    failures = violations(ROOT, json.loads(CONFIG.read_text(encoding="utf-8")))
    for failure in failures:
        print(f"complexity budget exceeded: {failure}")
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
