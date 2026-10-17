"""Compile a rendered site into a deterministic release bundle (app.json, edge.json, site.yaml)."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .bundle import build, bundle_sha256, verify
from .compose_policy import FIRST_PARTY
from .contracts import ValidationError
from .render import Rendered


def pin_first_party(model: Mapping[str, Any], image_refs: Mapping[str, str]) -> dict[str, Any]:
    """Point every first-party image at the digest this release built and attested.

    A module's own pin for ghcr.io/jon-robbins/homelab/<name> is only a placeholder: the release record's
    `images` map (image_refs) is what runs, so the bundle and the record always agree.
    """
    pinned = json.loads(json.dumps(model))
    for name, service in (pinned.get("services") or {}).items():
        image = service.get("image", "")
        if image.startswith(FIRST_PARTY):
            built = image.removeprefix(FIRST_PARTY).split("@")[0].split(":")[0]
            if built not in image_refs:
                raise ValidationError(f"$.services.{name}.image", f"no built digest for first-party image {built}")
            service["image"] = image_refs[built]
    return pinned


def compile_bundle(rendered: Rendered, site_yaml: Path, out: Path, image_refs: Mapping[str, str] | None = None) -> str:
    """Write the bundle to *out* and return its sha256."""
    with tempfile.TemporaryDirectory(prefix="release-compile-") as directory:
        root = Path(directory)
        for name, model in (("app.json", rendered.app), ("edge.json", rendered.edge)):
            text = json.dumps(pin_first_party(model, image_refs or {}), sort_keys=True, separators=(",", ":"))
            (root / name).write_text(text + "\n", encoding="utf-8")
        (root / "site.yaml").write_bytes(Path(site_yaml).read_bytes())
        data = build(root)
    verify(data)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return bundle_sha256(data)
