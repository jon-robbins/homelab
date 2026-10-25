"""compile_bundle: deterministic v2 bundles whose first-party images match the release record."""

import json
from pathlib import Path
import tarfile

import pytest

from scripts.release.compile import compile_bundle, pin_first_party
from scripts.release.contracts import ValidationError
from scripts.release.render import Rendered

SITE_YAML = Path(__file__).parents[2] / "examples/site/site.yaml"
BUILT = "ghcr.io/jon-robbins/homelab/caddy@sha256:" + "b" * 64
REFS = {"caddy": BUILT}


def models():
    app = {
        "services": {
            "proxy": {"image": "ghcr.io/jon-robbins/homelab/caddy@sha256:" + "0" * 64},
            "sonarr": {"image": "lscr.io/linuxserver/sonarr:4.0@sha256:" + "d" * 64},
        }
    }
    edge = {"services": {"caddy": {"image": "ghcr.io/jon-robbins/homelab/caddy@sha256:" + "0" * 64}}}
    return Rendered(app, edge)


def test_bundle_is_deterministic_and_has_the_v2_layout(tmp_path):
    first = compile_bundle(models(), SITE_YAML, tmp_path / "a.tar", REFS)
    assert compile_bundle(models(), SITE_YAML, tmp_path / "b.tar", REFS) == first
    assert (tmp_path / "a.tar").read_bytes() == (tmp_path / "b.tar").read_bytes()
    with tarfile.open(tmp_path / "a.tar") as archive:
        assert archive.getnames() == ["app.json", "edge.json", "site.yaml"]
        edge = json.loads(archive.extractfile("edge.json").read())
    assert edge["services"]["caddy"]["image"] == BUILT


def test_first_party_images_use_the_built_digest_and_upstream_images_are_untouched():
    app = pin_first_party(models().app, REFS)
    assert app["services"]["proxy"]["image"] == REFS["caddy"]
    assert app["services"]["sonarr"]["image"] == models().app["services"]["sonarr"]["image"]


def test_a_first_party_image_without_a_built_digest_is_an_error():
    with pytest.raises(ValidationError, match="no built digest for first-party image caddy"):
        pin_first_party(models().edge, {})
