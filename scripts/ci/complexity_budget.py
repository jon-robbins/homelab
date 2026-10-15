"""Check configured non-test Python source line budgets."""

import json
import sys
import tokenize
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/ci/budgets.json"
IGNORED_PARTS = {"test", "tests"}


def source_files(root: Path, path: str):
    target = root / path
    if target.is_file():
        candidates = [target]
    else:
        candidates = target.rglob("*.py")
    return (
        file
        for file in candidates
        if file.suffix == ".py"
        and not any(
            part in IGNORED_PARTS or part.startswith("test_")
            for part in file.relative_to(root).parts
        )
    )


def source_lines(path: Path) -> int:
    lines = set()
    with path.open(encoding="utf-8") as source:
        for token in tokenize.generate_tokens(source.readline):
            if token.type not in {
                tokenize.ENCODING,
                tokenize.ENDMARKER,
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.COMMENT,
            }:
                lines.add(token.start[0])
    return len(lines)


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
