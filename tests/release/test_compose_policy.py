import json
from pathlib import Path

import pytest

from scripts.release.compose_policy import check_policy, main, validate_compose_model
from scripts.release.contracts import ValidationError


FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.mark.parametrize(
    "name, message",
    [
        ("build", "build"),
        ("lifecycle", "lifecycle hooks"),
        ("pre_stop", "lifecycle hooks"),
        ("include", "remote includes"),
        ("env_file", "env_file"),
        ("unresolved", "unresolved variable"),
        ("docker_sock", "docker.sock"),
        ("privileged", "privileged"),
        ("devices", "devices"),
        ("cap_add", "cap_add"),
        ("network_mode", "network_mode"),
        ("pid_host", "pid"),
        ("ipc_host", "ipc"),
        ("short_bind", "short-syntax"),
        ("create_host_path", "create_host_path"),
        ("forbidden_path", "forbidden path"),
        ("forbidden_vpn", "forbidden path"),
        ("forbidden_home", "forbidden path"),
        ("forbidden_root", "forbidden path"),
    ],
)
def test_fixture_rejected(name, message, tmp_path):
    with pytest.raises(ValidationError, match=message):
        validate_compose_model(load(name), root=tmp_path)


def test_realistic_fixture_accepted(tmp_path):
    assert validate_compose_model(load("accepted"), root=tmp_path, bind_roots={"DATA_DIR": str(tmp_path / "data")})["services"]["web"]["image"]


def test_variable_expansion_and_symlink_into_homelab_rejected(tmp_path):
    source = tmp_path / "apparently-safe"
    source.symlink_to(Path("/srv/homelab"), target_is_directory=True)
    model = {
        "services": {
            "app": {
                "image": "example/app",
                "volumes": [{
                    "type": "bind",
                    "source": "${DATA_DIR}",
                    "target": "/data",
                    "bind": {"create_host_path": False},
                }],
            }
        }
    }
    with pytest.raises(ValidationError, match="/srv/homelab"):
        validate_compose_model(model, root=tmp_path, variables={"DATA_DIR": "apparently-safe"})


def test_expanded_docker_socket_is_rejected(tmp_path):
    model = {"services": {"app": {"image": "example/app", "command": "${SOCKET}"}}}
    with pytest.raises(ValidationError, match="docker.sock"):
        validate_compose_model(model, root=tmp_path, variables={"SOCKET": "/var/run/docker.sock"})


def _service(service, *, tier="app", **fields):
    return {"services": {service: {"image": "example/app", **fields}}}, f"modules/{tier}/{service}.yml"


def test_production_shaped_services_use_v2_allowlists(tmp_path):
    roots = {"APP_DATA_DIR": str(tmp_path / "data"), "MEDIA_HDD_PATH": "/srv/media", "MEDIA_NVME_PATH": "/srv/plex"}
    sonarr, path = _service("sonarr", volumes=[{"type": "bind", "source": "${APP_DATA_DIR}/sonarr", "target": "/config", "bind": {"create_host_path": False}}], cap_add=["CHOWN", "SETUID", "SETGID"])
    jellyfin, _ = _service("jellyfin", devices=["/dev/dri:/dev/dri"])
    plex, _ = _service("plex", volumes=[{"type": "bind", "source": "/srv/plex", "target": "/data", "bind": {"create_host_path": False}}], runtime="nvidia", deploy={"resources": {"reservations": {"devices": [{"capabilities": ["gpu"]}]}}})
    cloudflared, edge_path = _service("cloudflared", tier="edge", network_mode="host")
    for model, module in ((sonarr, path), (jellyfin, path), (plex, path), (cloudflared, edge_path)):
        validate_compose_model(model, root=tmp_path, variables=roots, bind_roots=roots, module_path=module)


@pytest.mark.parametrize("model, module, variables, message", [
    (_service("cloudflared", network_mode="host")[0], "modules/app/cloudflared.yml", {}, "host networking"),
    (_service("jellyfin", devices=["/dev/nvidia0:/dev/nvidia0"])[0], "modules/app/jellyfin.yml", {}, "device"),
    (_service("sonarr", cap_add=["SYS_ADMIN"])[0], "modules/app/sonarr.yml", {}, "capability"),
])
def test_production_shapes_reject_wrong_tier_or_unlisted_access(model, module, variables, message, tmp_path):
    with pytest.raises(ValidationError, match=message):
        validate_compose_model(model, root=tmp_path, variables=variables, bind_roots={}, module_path=module)


def test_declared_roots_are_required_and_protected(tmp_path):
    model, path = _service("sonarr", volumes=[{"type": "bind", "source": "${MEDIA_HDD_PATH}/sonarr", "target": "/data", "bind": {"create_host_path": False}}])
    with pytest.raises(ValidationError, match="outside declared site roots"):
        validate_compose_model(model, root=tmp_path, variables={"MEDIA_HDD_PATH": str(tmp_path / "media"), "APP_DATA_DIR": str(tmp_path / "data")}, bind_roots=["APP_DATA_DIR"], module_path=path)
    with pytest.raises(ValidationError, match="/srv/homelab"):
        validate_compose_model(model, root=tmp_path, variables={"MEDIA_HDD_PATH": "/srv/homelab"}, bind_roots=["MEDIA_HDD_PATH"], module_path=path)


def test_unused_declared_protected_root_is_rejected():
    with pytest.raises(ValidationError, match="declared root overlaps protected path /srv/vpn"):
        validate_compose_model({"services": {}}, bind_roots={"MEDIA_HDD_PATH": "/srv/vpn"})


@pytest.mark.parametrize("module_path, tier", [
    ("custom/edge/cloudflared.yml", None),
    ("modules/app/cloudflared.yml", "edge"),
    ("modules/edge/../app/cloudflared.yml", "edge"),
])
def test_host_network_requires_module_under_edge(module_path, tier):
    model, _ = _service("cloudflared", network_mode="host")
    with pytest.raises(ValidationError, match="host networking"):
        validate_compose_model(model, module_path=module_path, tier=tier)


def test_host_network_accepts_absolute_edge_module_path():
    model, path = _service("cloudflared", tier="edge", network_mode="host")
    validate_compose_model(model, module_path=Path(__file__).parents[2] / path)


def test_bind_without_declared_roots_is_rejected_by_api_and_cli(tmp_path, capsys):
    model = load("accepted")
    with pytest.raises(ValidationError, match="outside declared site roots"):
        validate_compose_model(model, root=tmp_path)
    source = tmp_path / "compose.json"
    source.write_text(json.dumps(model))
    assert main([str(source), "--root", str(tmp_path)]) == 1
    assert "outside declared site roots" in capsys.readouterr().out


@pytest.mark.parametrize("gpu_request", ["all", [{"count": 1, "capabilities": ["gpu"]}]])
def test_gpus_field_is_rejected(gpu_request):
    model, path = _service("jellyfin", gpus=gpu_request)
    with pytest.raises(ValidationError, match="gpus is not on the GPU allowlist"):
        validate_compose_model(model, module_path=path)


V2_ENV = {
    "APP_DATA_DIR": "/srv/homelab/data",
    "MEDIA_HDD_PATH": "/mnt/media-hdd",
    "MEDIA_PLEX_PATH": "/srv/plex",
    "HOMELAB_SECRET_DIR": "/srv/homelab/data/.secrets",
}
V2_ROOTS = ("APP_DATA_DIR", "MEDIA_HDD_PATH", "MEDIA_PLEX_PATH")


def test_check_policy_accepts_rendered_models():
    model = {
        "services": {
            "sonarr": {"cap_drop": ["ALL"], "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"], "volumes": [
                {"type": "bind", "source": "/srv/homelab/data/sonarr", "target": "/config"},
                {"type": "bind", "source": "/mnt/media-hdd/tv", "target": "/tv"},
            ]},
            "jellyfin": {"devices": ["/dev/dri:/dev/dri"], "gpus": "all"},
            "plex": {"gpus": [{"count": -1, "capabilities": ["gpu"]}], "volumes": [
                {"type": "bind", "source": "/srv/plex", "target": "/data"},
            ]},
            "cloudflared": {"network_mode": "host"},
            "overseerr": {},
        },
        "secrets": {"TMDB_API_KEY": {"file": "/srv/homelab/data/.secrets/TMDB_API_KEY"}},
    }
    check_policy(model, tiers={name: "edge" if name == "cloudflared" else "app" for name in model["services"]}, site_env=V2_ENV, bind_root_keys=V2_ROOTS)


@pytest.mark.parametrize("model, tiers, env, roots, message", [
    ({"services": {"app": {"network_mode": "host"}, "cloudflared": {"network_mode": "host"}}}, {"app": "app", "cloudflared": "edge"}, V2_ENV, V2_ROOTS, "host networking"),
    ({"services": {"app": {"cap_add": ["SYS_ADMIN"]}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "capability"),
    ({"services": {"app": {"devices": ["/dev/sda"]}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "device"),
    ({"services": {"app": {"runtime": "kata"}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "runtime"),
    ({"services": {"app": {"gpus": [{"capabilities": ["compute"]}]}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "GPU capability"),
    ({"services": {"app": {"gpus": "1"}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "GPU value"),
    ({"services": {"app": {"volumes": [{"type": "bind", "source": "/etc/x"}]}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "outside declared"),
    ({"services": {"app": {}}}, {"app": "app"}, {**V2_ENV, "MEDIA_HDD_PATH": "/srv/vpn"}, V2_ROOTS, "protected"),
    ({"services": {"app": {}}}, {}, V2_ENV, V2_ROOTS, "tier"),
    ({"services": {"app": {}}, "secrets": {"X": {"file": "/tmp/X"}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "secret"),
    ({"services": {"app": {"privileged": True}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "privileged"),
])
def test_check_policy_rejects_each_v2_violation(model, tiers, env, roots, message):
    with pytest.raises(ValidationError, match=message):
        check_policy(model, tiers=tiers, site_env=env, bind_root_keys=roots)
