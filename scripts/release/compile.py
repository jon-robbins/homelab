"""Compile a rendered site into a deterministic release bundle."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile

from .bundle import build, bundle_sha256, verify
from .render import Rendered


def compile_bundle(rendered: Rendered, site_yaml: Path, out: Path) -> str:
    """Write ``app.json``, ``edge.json``, and ``site.yaml`` to *out*."""
    if not isinstance(rendered, Rendered):
        raise TypeError("rendered must be a Rendered value")
    with tempfile.TemporaryDirectory(prefix="release-compile-") as directory:
        root = Path(directory)
        for name, model in (("app.json", rendered.app), ("edge.json", rendered.edge)):
            (root / name).write_text(
                json.dumps(model, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
            )
        shutil.copyfile(site_yaml, root / "site.yaml")
        data = build(root)
    verify(data)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return bundle_sha256(data)
