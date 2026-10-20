"""Contract tests for modules.py: module file layout, variables, plugins and sites.

What a service may contain (pins, health, binds, caps) is tested on rendered models in test_compose_policy.py.
"""

from pathlib import Path
import re

import pytest
import yaml

from scripts.release.contracts import ValidationError
from scripts.release.modules import check_module, check_variables, load_plugin, load_site, read_env

ROOT = Path(__file__).parents[2]
ENV = {"APP_DATA_DIR": "/srv/homelab/data", "MEDIA_HDD_PATH": "/mnt/media-hdd"}


def write_module(root: Path, service="whoami", tier="app", body=None) -> Path:
    path = root / "modules" / tier / f"{service}.compose.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = (
        body if body is not None else {"services": {service: {"image": "x", "volumes": ["${APP_DATA_DIR}/w:/w"]}}}
    )
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def write_plugin(root: Path, *, readme="whoami serves requests", metadata=None, service="whoami") -> Path:
    plugin = root / "plugin"
    plugin.mkdir(parents=True)
    meta = metadata if metadata is not None else {"contract": 1, "name": "plugin", "description": "test"}
    (plugin / "homelab-plugin.yaml").write_text(yaml.safe_dump(meta), encoding="utf-8")
    (plugin / "README.md").write_text(readme, encoding="utf-8")
    write_module(plugin, service)
    return plugin


def write_site(root: Path, *, env="APP_DATA_DIR=/srv/homelab/data\n", **changes) -> Path:
    site = root / "site"
    site.mkdir(parents=True, exist_ok=True)
    config = {
        "contract": 1,
        "site_id": "test",
        "bind_roots": ["APP_DATA_DIR"],
        "plugins": {},
        "services": {},
        "managed": [],
    }
    (site / "site.yaml").write_text(yaml.safe_dump({**config, **changes}), encoding="utf-8")
    (site / "site.env").write_text(env, encoding="utf-8")
    return site


# --- modules -------------------------------------------------------------------------------------------


def test_module_returns_its_tier(tmp_path):
    assert check_module(write_module(tmp_path, tier="edge"), ENV) == "edge"


@pytest.mark.parametrize(
    "path, body, message",
    [
        ("modules/app/whoami.yml", None, "modules/app/whoami.compose.yml"),
        ("modules/app/whoami.yaml", None, "modules/app/whoami.compose.yml"),
        ("modules/other/whoami.compose.yml", None, "modules/<app\\|edge>/<svc>\\.compose\\.yml"),
        ("modules/app/whoami.compose.yml", {"services": {"a": {}, "b": {}}}, "exactly one service"),
        ("modules/app/whoami.compose.yml", {"services": {"other": {}}}, "named whoami"),
    ],
)
def test_module_layout_is_enforced(tmp_path, path, body, message):
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(yaml.safe_dump(body or {"services": {"whoami": {}}}), encoding="utf-8")
    with pytest.raises(ValidationError, match=message):
        check_module(target, ENV)


@pytest.mark.parametrize(
    "text, message",
    [
        ("${UNKNOWN}/x", "undeclared site.env key UNKNOWN"),
        ("${APP_DATA_DIR:-/tmp}", "must use \\$\\{NAME\\}"),
        ("$APP_DATA_DIR/x", "must use \\$\\{NAME\\}"),
    ],
)
def test_variables_must_be_declared_plain_references(text, message):
    assert any(re.search(message, str(error)) for error in check_variables(text, ENV, "m"))


def test_double_dollar_is_a_literal():
    assert check_variables("echo $$HOME ${APP_DATA_DIR}", ENV, "m") == []


def test_read_env_skips_comments_and_blank_lines(tmp_path):
    (tmp_path / "site.env").write_text("# note\n\nA=1\nB=x=y\n", encoding="utf-8")
    assert read_env(tmp_path / "site.env") == {"A": "1", "B": "x=y"}


# --- plugins -------------------------------------------------------------------------------------------


def test_valid_plugin_is_loaded(tmp_path):
    plugin = load_plugin(
        "plugin",
        write_plugin(
            tmp_path,
            metadata={"contract": 1, "name": "plugin", "description": "d", "image_namespace": "ghcr.io/me/plugin"},
        ),
        ENV,
        tmp_path / "platform",
    )
    assert plugin.image_namespace == "ghcr.io/me/plugin"


@pytest.mark.parametrize(
    "metadata, message",
    [
        ({"contract": 1, "name": "plugin"}, "description: missing"),
        ({"contract": 1, "name": "plugin", "description": "d", "extra": 1}, "extra: unknown field"),
        ({"contract": 2, "name": "plugin", "description": "d"}, "contract must be 1"),
        ({"contract": 1, "name": "Plugin!", "description": "d"}, "lowercase id"),
        ({"contract": 1, "name": "plugin", "description": "d", "image_namespace": ""}, "image_namespace non-empty"),
    ],
)
def test_plugin_metadata_is_closed(tmp_path, metadata, message):
    with pytest.raises(ValidationError, match=message):
        load_plugin("plugin", write_plugin(tmp_path, metadata=metadata), ENV, tmp_path / "platform")


@pytest.mark.parametrize("readme", ["", "whoami-extra only"])
def test_plugin_readme_must_name_each_service(tmp_path, readme):
    with pytest.raises(ValidationError, match="does not mention service whoami"):
        load_plugin("plugin", write_plugin(tmp_path, readme=readme), ENV, tmp_path / "platform")


def test_plugin_cannot_reuse_a_platform_module_name(tmp_path):
    write_module(tmp_path / "platform")
    with pytest.raises(ValidationError, match="reuses a platform module name"):
        load_plugin("plugin", write_plugin(tmp_path), ENV, tmp_path / "platform")


def test_plugin_must_ship_a_module(tmp_path):
    plugin = write_plugin(tmp_path)
    (plugin / "modules/app/whoami.compose.yml").unlink()
    with pytest.raises(ValidationError, match="must ship a module"):
        load_plugin("plugin", plugin, ENV, tmp_path / "platform")


# --- sites ---------------------------------------------------------------------------------------------


def test_examples_are_valid():
    site = load_site(ROOT / "examples/site", ROOT)
    assert site.config["site_id"] == "example" and set(site.plugins) == {"plugin-hello"}


@pytest.mark.parametrize("tier", ["app", "edge"])
def test_site_rejects_platform_legacy_module_without_selected_services(tmp_path, tier):
    site = write_site(tmp_path)
    legacy = tmp_path / "modules" / tier / "whoami.yml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(ValidationError, match=f"modules/{tier}/whoami\\.compose\\.yml"):
        load_site(site, tmp_path)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"site_id": "Bad Id"}, "site_id a lowercase id"),
        ({"surprise": 1}, "surprise: unknown field"),
        ({"bind_roots": ["HOME"]}, "bind_roots: each must be one of"),
        ({"marker_roots": ["MEDIA_HDD_PATH"]}, "marker roots must be bind roots"),
        ({"plugins": {"p": {"path": "a", "repository": "b"}}}, "must be \\{path: DIR\\} or"),
        ({"plugins": {"p": {"url": "x"}}}, "must be \\{path: DIR\\} or"),
        ({"services": {"svc": {"state": "paused"}}}, "must be running or stopped"),
        ({"services": {"svc": {"form": "typo"}}}, "form: unknown field"),
    ],
)
def test_site_config_is_closed(tmp_path, changes, message):
    with pytest.raises(ValidationError, match=message):
        load_site(write_site(tmp_path, **changes), tmp_path)


def test_unknown_site_env_key_is_rejected(tmp_path):
    with pytest.raises(ValidationError, match="site.env.TYPO_DIR: unknown key"):
        load_site(write_site(tmp_path, env="APP_DATA_DIR=/x\nTYPO_DIR=/y\n"), tmp_path)


def test_repository_plugin_must_be_supplied_and_is_then_validated(tmp_path):
    site = write_site(tmp_path, plugins={"plugin": {"repository": "owner/plugin"}})
    with pytest.raises(ValidationError, match="repository plugin was not supplied"):
        load_site(site, tmp_path)
    plugin = write_plugin(tmp_path / "checkout", readme="nothing here")
    with pytest.raises(ValidationError, match="does not mention service whoami"):
        load_site(site, tmp_path, {"plugin": plugin})
