import json

import pytest

from scripts.release.contracts import ValidationError, load_release_record, parse_json


SHA = "a" * 40


def record(**overrides):
    value = {
        "schema": 1,
        "site_id": "home",
        "sequence": 3,
        "tooling_git_sha": SHA,
        "sources": {"homelab": SHA, "homelab-site": "b" * 40, "plugin-hello": "c" * 40},
        "images": {},
        "bundle_sha256": "sha256:" + "d" * 64,
        "rollback_floor": 2,
    }
    value.update(overrides)
    return value


def test_release_record_accepts_plugin_sources():
    assert load_release_record(json.dumps(record()))["sources"]["plugin-hello"] == "c" * 40


def test_release_record_requires_platform_sources():
    with pytest.raises(ValidationError, match=r"^\$\.sources\.homelab"):
        load_release_record(json.dumps(record(sources={"homelab-site": SHA})))


def test_release_record_rejects_invalid_image():
    with pytest.raises(ValidationError, match=r"^\$\.images\.web"):
        load_release_record(json.dumps(record(images={"web": "latest"})))


def test_duplicate_json_key_rejected():
    with pytest.raises(ValidationError, match=r"^\$: duplicate"):
        parse_json('{"schema": 1, "schema": 1}')
