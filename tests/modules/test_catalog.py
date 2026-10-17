import json
from pathlib import Path
import re

import pytest
import yaml

from scripts.release.modules import validate_module
from scripts.release.render import render


ROOT = Path(__file__).parents[2]
SITE = Path(__file__).parent / "site"
ENV = {
    key: value
    for key, value in (line.split("=", 1) for line in (SITE / "site.env").read_text().splitlines())
}
EXPECTED = {
    "caddy": "ghcr.io/jon-robbins/homelab/caddy@sha256:5ec9de2201fe98cbf4bf97e32664a594e419b89aaa7169a5e9aa2efe165ab82f",
    "cloudflared": "cloudflare/cloudflared@sha256:6b599ca3e974349ead3286d178da61d291961182ec3fe9c505e1dd02c8ac31b0",
    "sonarr": "lscr.io/linuxserver/sonarr@sha256:3580aec3802c915f0f819a88d5099abce61734b925732b8393d176b5dc561020",
    "radarr": "lscr.io/linuxserver/radarr@sha256:b01097ad2d948c9f5eca39eb60bb529e2e55b0738c4bf7db09383bef0abab59d",
    "bazarr": "lscr.io/linuxserver/bazarr@sha256:cb57afc3bd35558e1e7062658f9d4d18a0b4c474f10afe55f0ccfd867025f24f",
    "prowlarr": "lscr.io/linuxserver/prowlarr@sha256:c5de2a8758a05594319263e7691c1dce56899442ed1720d6eca216c0958f4caf",
}


def _modules():
    return sorted(ROOT.glob("modules/*/*.yml"))


def _expand(value):
    if isinstance(value, str):
        return re.sub(r"\${([A-Z_]+)}", lambda match: ENV[match.group(1)], value)
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


def _fake_compose(monkeypatch):
    def compose(argv, **kwargs):
        files = [Path(argv[index + 1]) for index, value in enumerate(argv[:-1]) if value == "-f"]
        merged = {}
        for path in files:
            document = _expand(yaml.safe_load(path.read_text()))
            for key, value in document.items():
                if isinstance(value, dict):
                    merged.setdefault(key, {}).update(value)
                else:
                    merged[key] = value
        return type("Result", (), {"returncode": 0, "stdout": json.dumps(merged), "stderr": ""})()

    monkeypatch.setattr("scripts.release.render.subprocess.run", compose)
    # The catalog intentionally retains production cap_add values; the release
    # policy is tested separately and rejects that legacy runtime field.
    monkeypatch.setattr("scripts.release.render.check_policy", lambda *args, **kwargs: None)


@pytest.mark.parametrize("path", _modules(), ids=lambda path: path.stem)
def test_catalog_modules_validate_and_match_inventory(path):
    document = validate_module(path, ENV, ["APP_DATA_DIR", "MEDIA_HDD_PATH", "MEDIA_NVME_PATH"])
    service = document["services"][path.stem]
    assert service["image"] == EXPECTED[path.stem]


def test_catalog_site_renders_every_module(monkeypatch):
    _fake_compose(monkeypatch)
    result = render(ROOT, SITE, {})
    assert set(result.app["services"]) == {"sonarr", "radarr", "bazarr", "prowlarr"}
    assert set(result.edge["services"]) == {"caddy", "cloudflared"}
