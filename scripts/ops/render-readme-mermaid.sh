#!/usr/bin/env bash
set -euo pipefail
exec "$(dirname "$0")/render-docs-mermaid.sh" README.md
