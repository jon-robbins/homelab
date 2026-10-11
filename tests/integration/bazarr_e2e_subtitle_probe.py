#!/usr/bin/env python3
"""CLI wrapper for Bazarr subtitle probe (implementation in homelab_workers)."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "apps" / "homelab-workers" / "src"
if _SRC.is_dir():
    _ins = str(_SRC)
    if _ins not in sys.path:
        sys.path.insert(0, _ins)

from homelab_workers.integration.bazarr_subtitles import cli_main

if __name__ == "__main__":
    raise SystemExit(cli_main())
