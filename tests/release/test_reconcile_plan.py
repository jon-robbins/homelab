import pytest

from scripts.release import reconcile
from scripts.release.verify import VerificationError, VerificationResult


class Client:
    def __init__(self, previous, heads):
        self.previous = previous
        self.heads = heads

    def last_record(self, _repository, _branch):
        return self.previous

    def ref(self, repository, _branch):
        if repository == "site":
            return self.heads["homelab-site"]
        if repository.endswith("tracker-integrations"):
            return self.heads["tracker-integrations"]
        return self.heads[repository]


def _record(sources, images, *, rollback_floor=4):
    return {
        "sequence": 7,
        "sources": sources,
        "images": images,
        "rollback_floor": rollback_floor,
    }


def _setup(monkeypatch, images):
    monkeypatch.setattr(reconcile, "load_approved_destinations", lambda: {
        f"ghcr.io/jon-robbins/homelab/{image}" for image in images
    })
    monkeypatch.setattr(reconcile, "_materialize_site_inputs", lambda *_args: None)
    monkeypatch.setattr(reconcile, "_manifest_site_id", lambda _root: "lab")
    monkeypatch.setattr(
        reconcile,
        "verify_release",
        lambda _client, _sources, builds, **_kwargs: VerificationResult(
            (), {image: spec["manifest_digest"] for image, spec in builds.items()}, dict(builds)
        ),
    )


def _build(image, digest):
    return {
        "repository": "homelab",
        "head_sha": "h2",
        "run_id": 42,
        "artifact_name": f"oci-{image}",
        "image": image,
        "manifest_digest": digest,
    }


def test_carries_forward_unchanged_images(monkeypatch, tmp_path):
    images = {"agent", "mcp"}
    previous = _record(
        {"homelab": "h1", "homelab-site": "s1", "tracker-integrations": "t1"},
        {"agent": "ghcr.io/jon-robbins/homelab/agent@sha256:" + "a" * 64,
         "mcp": "ghcr.io/jon-robbins/homelab/mcp@sha256:" + "b" * 64},
    )
    client = Client(previous, {"homelab": "h2", "homelab-site": "s1", "tracker-integrations": "t1"})
    _setup(monkeypatch, images)
    monkeypatch.setattr(reconcile, "_attested_builds", lambda *_args: {"agent": _build("agent", "sha256:" + "c" * 64)})

    result = reconcile.plan(client, repository="homelab", site_repository="site", output=tmp_path / "plan.json")

    assert result["image_refs"]["mcp"] == previous["images"]["mcp"]
    assert result["image_refs"]["agent"].endswith("@sha256:" + "c" * 64)
    assert result["rollback_floor"] == 4


def test_first_release_requires_every_approved_image(monkeypatch):
    images = {"agent", "mcp"}
    client = Client(None, {"homelab": "h2", "homelab-site": "s1", "tracker-integrations": "t1"})
    _setup(monkeypatch, images)
    monkeypatch.setattr(reconcile, "_attested_builds", lambda *_args: {"agent": _build("agent", "sha256:" + "a" * 64)})

    with pytest.raises(VerificationError, match="first release requires verified builds for all images"):
        reconcile.plan(client, repository="homelab", site_repository="site")


def test_site_only_change_preserves_full_image_map(monkeypatch):
    images = {"agent", "mcp"}
    image_map = {
        "agent": "ghcr.io/jon-robbins/homelab/agent@sha256:" + "a" * 64,
        "mcp": "ghcr.io/jon-robbins/homelab/mcp@sha256:" + "b" * 64,
    }
    sources = {"homelab": "h1", "homelab-site": "s1", "tracker-integrations": "t1"}
    previous = _record(sources, image_map)
    client = Client(previous, {"homelab": "h1", "homelab-site": "s2", "tracker-integrations": "t1"})
    _setup(monkeypatch, images)

    result = reconcile.plan(client, repository="homelab", site_repository="site")

    assert result["release"] is True
    assert result["images"] == []
    assert result["image_refs"] == image_map
    assert result["site_id"] == "lab"


def test_homelab_only_change_without_image_build_preserves_full_image_map(monkeypatch):
    images = {"agent", "mcp"}
    image_map = {
        "agent": "ghcr.io/jon-robbins/homelab/agent@sha256:" + "a" * 64,
        "mcp": "ghcr.io/jon-robbins/homelab/mcp@sha256:" + "b" * 64,
    }
    sources = {"homelab": "h1", "homelab-site": "s1", "tracker-integrations": "t1"}
    previous = _record(sources, image_map)
    client = Client(previous, {"homelab": "h2", "homelab-site": "s1", "tracker-integrations": "t1"})
    _setup(monkeypatch, images)
    monkeypatch.setattr(reconcile, "_attested_builds", lambda *_args: {})

    result = reconcile.plan(client, repository="homelab", site_repository="site")

    assert result["release"] is True
    assert result["images"] == []
    assert result["image_refs"] == image_map


def test_no_source_change_produces_no_release(monkeypatch):
    sources = {"homelab": "h1", "homelab-site": "s1", "tracker-integrations": "t1"}
    image_map = {"agent": "ghcr.io/jon-robbins/homelab/agent@sha256:" + "a" * 64}
    previous = _record(sources, image_map)
    client = Client(previous, sources)
    _setup(monkeypatch, {"agent"})

    result = reconcile.plan(client, repository="homelab", site_repository="site")

    assert result == {
        "release": False,
        "sha": "h1",
        "sequence": 8,
        "images": [],
        "image_refs": image_map,
    }
