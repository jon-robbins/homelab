#!/usr/bin/env bash
# Compatibility entrypoint for callers of the retired local CI command.
# The source of truth is now pre-commit plus nox.
set -euo pipefail

scope="${1:-validate}"
if [[ "$scope" != "validate" ]]; then
  echo "FATAL: only the validate compatibility scope is supported" >&2
  exit 2
fi

for argument in "${@:2}"; do
  [[ "$argument" == "--no-docker" ]] || {
    echo "FATAL: unsupported argument '$argument'" >&2
    exit 2
  }
done

if command -v pre-commit >/dev/null 2>&1 && command -v nox >/dev/null 2>&1; then
  pre-commit run --all-files
  nox -t no-docker
elif command -v uv >/dev/null 2>&1; then
  uv run --with pre-commit==4.2.0 --with nox==2025.2.9 pre-commit run --all-files
  uv run --with nox==2025.2.9 nox -t no-docker
else
  echo "FATAL: install pinned pre-commit and nox (or uv)" >&2
  exit 1
fi
