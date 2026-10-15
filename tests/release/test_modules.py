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
    path.parent.mkdir(parents=True)
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


def test_config_files_are_not_host_binds(tmp_path):
    path = write_module(tmp_path)
    document = yaml.safe_load(path.read_text())
    document["services"]["whoami"]["configs"] = [{"source": "settings", "target": "/etc/settings"}]
    document["configs"] = {"settings": {"file": "/repo/site/files/settings"}}
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    validate_module(path, ENV)


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
        validate_site(site)


@pytest.mark.parametrize("source", [{"path": 42}, {"repository": 42}])
def test_site_plugin_source_types_use_schema(tmp_path, source):
    site = tmp_path / "site"
    site.mkdir()
    document = {"contract": 1, "site_id": "test", "plugins": {"bad": source},
                "services": {}, "managed": []}
    (site / "site.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValidationError, match="expected string"):
        validate_site(site)


def test_examples_are_valid():
    assert validate_site(ROOT / "examples/site")["site_id"] == "example"
