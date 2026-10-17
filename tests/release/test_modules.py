from pathlib import Path

import pytest
import yaml

from scripts.release.contracts import ValidationError
from scripts.release.modules import validate_module, validate_plugin, validate_site


ROOT = Path(__file__).parents[2]
IMAGE = "traefik/whoami@sha256:" + "a" * 64
ENV = {"APP_DATA_DIR": "/srv/homelab/data", "MEDIA_HDD_PATH": "/srv/media"}


def write_module(root, *, service="whoami", image=IMAGE, volume="${APP_DATA_DIR}/whoami", health=True, state=True):
    path = root / "modules" / "app" / f"{service}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    service_doc = {"image": image, "volumes": [f"{volume}:/data"]}
    if health:
        service_doc["healthcheck"] = {"test": ["CMD", "true"]}
    if state:
        service_doc["labels"] = {"homelab.state": "stateless"}
    path.write_text(yaml.safe_dump({"services": {service: service_doc}}), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "kwargs",
    [
        {"volume": "/outside/data"},
        {"image": "traefik/whoami:latest"},
        {"health": False},
        {"state": False},
    ],
)
def test_module_rejects_broken_contract_rules(tmp_path, kwargs):
    with pytest.raises(ValidationError):
        validate_module(write_module(tmp_path, **kwargs), ENV)


def test_module_rejects_wrong_path(tmp_path):
    path = tmp_path / "wrong.yml"
    document = {"services": {"wrong": {
        "image": IMAGE, "healthcheck": {"test": ["CMD", "true"]},
        "labels": {"homelab.state": "stateless"},
    }}}
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValidationError, match="modules/<app\\|edge>"):
        validate_module(path, ENV)


@pytest.mark.parametrize("healthcheck", [None, {"disable": True}, {"test": ["NONE"]}])
def test_disabled_healthcheck_requires_probe(tmp_path, healthcheck):
    path = write_module(tmp_path)
    document = yaml.safe_load(path.read_text())
    document["services"]["whoami"]["healthcheck"] = healthcheck
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValidationError, match="healthcheck or homelab.probe"):
        validate_module(path, ENV)


def test_numeric_probe_satisfies_health_rule(tmp_path):
    path = write_module(tmp_path, health=False)
    document = yaml.safe_load(path.read_text())
    document["services"]["whoami"]["labels"]["homelab.probe"] = "http://:8080/health"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    validate_module(path, ENV)


def test_non_numeric_probe_port_is_rejected(tmp_path):
    path = write_module(tmp_path, health=False)
    document = yaml.safe_load(path.read_text())
    document["services"]["whoami"]["labels"]["homelab.probe"] = "http://:web/health"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValidationError, match="healthcheck or homelab.probe"):
        validate_module(path, ENV)


def test_tilde_bind_source_is_rejected(tmp_path):
    path = write_module(tmp_path, volume="~/whoami")
    with pytest.raises(ValidationError, match=r"use \$\{NAME\} roots"):
        validate_module(path, ENV)


def test_config_files_are_not_host_binds(tmp_path):
    path = write_module(tmp_path)
    document = yaml.safe_load(path.read_text())
    document["services"]["whoami"]["configs"] = [{"source": "settings", "target": "/etc/settings"}]
    document["configs"] = {"settings": {"file": "/repo/site/files/settings"}}
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    validate_module(path, ENV)


def test_media_note_is_not_a_bind_root(tmp_path):
    path = write_module(tmp_path, volume="/etc/passwd")
    with pytest.raises(ValidationError, match="outside site.env data/media roots"):
        validate_module(path, {**ENV, "MEDIA_NOTE": "/etc"})


def test_undeclared_variable_in_environment_is_rejected(tmp_path):
    path = write_module(tmp_path)
    document = yaml.safe_load(path.read_text())
    document["services"]["whoami"]["environment"] = {"VALUE": "${TZ}"}
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValidationError, match="undeclared site.env key TZ"):
        validate_module(path, ENV)


@pytest.mark.parametrize("value", ["$APP_DATA_DIR/whoami", "${APP_DATA_DIR:-/tmp}"])
def test_noncanonical_interpolation_is_rejected(tmp_path, value):
    path = write_module(tmp_path, volume=value)
    with pytest.raises(ValidationError, match="variables must use"):
        validate_module(path, ENV)


def test_named_volume_is_not_treated_as_a_host_bind(tmp_path):
    path = write_module(tmp_path, volume="whoami-data")
    validate_module(path, ENV)


def test_site_bind_roots_select_exact_env_keys(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    write_plugin(site)
    plugin = site / "plugin"
    (site / "site.env").write_text("APP_DATA_DIR=/srv/data\nMEDIA_HDD_PATH=/etc\n", encoding="utf-8")
    document = {"contract": 1, "site_id": "site", "bind_roots": ["APP_DATA_DIR"],
                "plugins": {"plugin": {"path": "plugin"}}, "services": {}, "managed": []}
    (site / "site.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    module = plugin / "modules/app/whoami.yml"
    compose = yaml.safe_load(module.read_text())
    compose["services"]["whoami"]["volumes"] = ["${MEDIA_HDD_PATH}/file:/data"]
    module.write_text(yaml.safe_dump(compose), encoding="utf-8")
    with pytest.raises(ValidationError, match="outside site.env data/media roots"):
        validate_site(site, tmp_path)


def test_site_with_empty_bind_roots_rejects_bind(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    write_plugin(site)
    (site / "site.env").write_text("APP_DATA_DIR=/srv/homelab/data\n", encoding="utf-8")
    document = {"contract": 1, "site_id": "site", "bind_roots": [],
                "plugins": {"plugin": {"path": "plugin"}}, "services": {}, "managed": []}
    (site / "site.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValidationError, match="outside site.env data/media roots"):
        validate_site(site, tmp_path)


def write_plugin(root, readme="whoami", service="whoami"):
    plugin = root / "plugin"
    plugin.mkdir()
    (plugin / "homelab-plugin.yaml").write_text(
        "contract: 1\nname: plugin\ndescription: test\n", encoding="utf-8"
    )
    (plugin / "README.md").write_text(readme, encoding="utf-8")
    write_module(plugin, service=service)
    return plugin


def test_plugin_rejects_readme_without_exact_service_name(tmp_path):
    with pytest.raises(ValidationError, match="README does not mention service whoami"):
        validate_plugin(write_plugin(tmp_path, readme="whoami-extra"), ENV)


def test_plugin_rejects_platform_name_collision(tmp_path):
    with pytest.raises(ValidationError, match="collides with platform module"):
        validate_plugin(write_plugin(tmp_path), ENV, {"whoami"})


def test_code_plugin_rejects_image_outside_declared_namespace(tmp_path):
    plugin = write_plugin(tmp_path)
    metadata = plugin / "homelab-plugin.yaml"
    metadata.write_text(metadata.read_text() + "image_namespace: ghcr.io/owner/plugin\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="outside ghcr.io/owner/plugin"):
        validate_plugin(plugin, ENV)


def test_code_plugin_rejects_empty_namespace(tmp_path):
    plugin = write_plugin(tmp_path)
    metadata = plugin / "homelab-plugin.yaml"
    metadata.write_text(metadata.read_text() + 'image_namespace: ""\n', encoding="utf-8")
    with pytest.raises(ValidationError, match="image for whoami is outside"):
        validate_plugin(plugin, ENV)


def test_code_plugin_accepts_image_in_declared_namespace(tmp_path):
    plugin = write_plugin(tmp_path)
    metadata = plugin / "homelab-plugin.yaml"
    metadata.write_text(metadata.read_text() + "image_namespace: ghcr.io/owner/plugin\n", encoding="utf-8")
    write_module(plugin, image="ghcr.io/owner/plugin/worker@sha256:" + "a" * 64)
    validate_plugin(plugin, ENV)


def test_code_plugin_rejects_image_equal_to_declared_namespace(tmp_path):
    plugin = write_plugin(tmp_path)
    metadata = plugin / "homelab-plugin.yaml"
    metadata.write_text(metadata.read_text() + "image_namespace: ghcr.io/owner/plugin\n", encoding="utf-8")
    write_module(plugin, image="ghcr.io/owner/plugin@sha256:" + "a" * 64)
    with pytest.raises(ValidationError, match="outside ghcr.io/owner/plugin"):
        validate_plugin(plugin, ENV)


def test_site_validates_supplied_repository_plugin_directory(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    plugin = write_plugin(tmp_path)
    metadata = plugin / "homelab-plugin.yaml"
    metadata.write_text(metadata.read_text() + "image_namespace: ghcr.io/owner/plugin\n", encoding="utf-8")
    (site / "site.env").write_text("APP_DATA_DIR=/srv/homelab/data\n", encoding="utf-8")
    (site / "site.yaml").write_text(
        "contract: 1\nsite_id: site\nplugins:\n  plugin: {repository: owner/plugin}\n"
        "services: {}\nmanaged: []\nbind_roots: [APP_DATA_DIR]\n", encoding="utf-8"
    )
    with pytest.raises(ValidationError, match="outside ghcr.io/owner/plugin"):
        validate_site(site, tmp_path, {"plugin": plugin})


def test_separate_site_uses_supplied_platform_catalog(tmp_path):
    site = tmp_path / "separate-site"
    site.mkdir()
    platform = tmp_path / "platform"
    write_module(platform)
    write_plugin(site)
    (site / "site.env").write_text("APP_DATA_DIR=/srv/homelab/data\n", encoding="utf-8")
    (site / "site.yaml").write_text(
        "contract: 1\nsite_id: separate\nplugins:\n  plugin: {path: plugin}\n"
        "services:\n  whoami: {}\nmanaged: [whoami]\nbind_roots: [APP_DATA_DIR]\n", encoding="utf-8"
    )
    with pytest.raises(ValidationError, match="collides with platform module"):
        validate_site(site, platform)


def test_schemas_are_applied(tmp_path):
    plugin = write_plugin(tmp_path)
    metadata = yaml.safe_load((plugin / "homelab-plugin.yaml").read_text())
    metadata["unexpected"] = True
    (plugin / "homelab-plugin.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    with pytest.raises(ValidationError, match="unknown field"):
        validate_plugin(plugin, ENV)

    site = tmp_path / "site"
    site.mkdir()
    (site / "site.yaml").write_text("contract: 1\nsite_id: test\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="missing required field"):
        validate_site(site, tmp_path)


@pytest.mark.parametrize("source", [{"path": 42}, {"repository": 42}])
def test_site_plugin_source_types_use_schema(tmp_path, source):
    site = tmp_path / "site"
    site.mkdir()
    document = {"contract": 1, "site_id": "test", "plugins": {"bad": source},
                "services": {}, "managed": [], "bind_roots": []}
    (site / "site.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValidationError, match="expected string"):
        validate_site(site, tmp_path)


def test_examples_are_valid():
    assert validate_site(ROOT / "examples/site", ROOT)["site_id"] == "example"
