import json
import os
from pathlib import Path

import pytest

from scripts.release.compose_policy import check_policy, validate_compose_model
from scripts.release.contracts import ValidationError, ValidationErrors


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
    assert validate_compose_model(load("accepted"), root=tmp_path)["services"]["web"]["image"]


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


V2_ENV = {
    "APP_DATA_DIR": "/srv/homelab/data",
    "MEDIA_HDD_PATH": "/mnt/media-hdd",
    "MEDIA_PLEX_PATH": "/srv/plex",
    "HOMELAB_SECRET_DIR": "/srv/homelab/data/.secrets",
}
V2_ROOTS = ("APP_DATA_DIR", "MEDIA_HDD_PATH", "MEDIA_PLEX_PATH")


@pytest.mark.parametrize("model, tiers", [
    ({"services": {"sonarr": {
        "cap_drop": ["ALL"],
        "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
        "volumes": [
            {"type": "bind", "source": "/srv/homelab/data/sonarr", "target": "/config"},
            {"type": "bind", "source": "/mnt/media-hdd/tv", "target": "/tv"},
        ],
    }}}, {"sonarr": "app"}),
    ({"services": {"jellyfin": {"devices": ["/dev/dri:/dev/dri"], "gpus": "all"}}}, {"jellyfin": "app"}),
    ({"services": {"plex": {
        "gpus": [{"count": -1, "capabilities": ["gpu"]}],
        "volumes": [{"type": "bind", "source": "/srv/plex", "target": "/data"}],
    }}}, {"plex": "app"}),
    ({"services": {"cloudflared": {"network_mode": "host"}}}, {"cloudflared": "edge"}),
    ({"services": {"overseerr": {}}, "secrets": {
        "TMDB_API_KEY": {"file": "/srv/homelab/data/.secrets/TMDB_API_KEY"},
    }}, {"overseerr": "app"}),
])
def test_check_policy_accepts_rendered_model(model, tiers):
    check_policy(model, tiers=tiers, site_env=V2_ENV, bind_root_keys=V2_ROOTS)


@pytest.mark.parametrize("model, tiers, env, roots, path", [
    ({"services": {"cloudflared": {"network_mode": "host"}}}, {"cloudflared": "app"}, V2_ENV, V2_ROOTS, "$.services.cloudflared.network_mode"),
    ({"services": {"app": {"network_mode": "host"}, "cloudflared": {"network_mode": "host"}}}, {"app": "app", "cloudflared": "edge"}, V2_ENV, V2_ROOTS, "$.services.app.network_mode"),
    ({"services": {"app": {"cap_add": ["SYS_ADMIN"]}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "$.services.app.cap_add[0]"),
    ({"services": {"app": {"devices": ["/dev/sda"]}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "$.services.app.devices[0]"),
    ({"services": {"app": {"runtime": "kata"}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "$.services.app.runtime"),
    ({"services": {"app": {"gpus": [{"driver": "amd", "capabilities": ["gpu"]}]}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "$.services.app.gpus[0].driver"),
    ({"services": {"app": {"gpus": [{"capabilities": ["compute"]}]}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "$.services.app.gpus[0].capabilities"),
    ({"services": {"app": {"gpus": "1"}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "$.services.app.gpus"),
    ({"services": {"app": {"volumes": [{"type": "bind", "source": "/etc/x"}]}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "$.services.app.volumes[0].source"),
    ({"services": {"app": {"volumes": [{"type": "bind", "source": "relative/x"}]}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "$.services.app.volumes[0].source"),
    ({"services": {"app": {}}}, {"app": "app"}, {**V2_ENV, "MEDIA_HDD_PATH": "/srv/vpn"}, V2_ROOTS, "$.site_env.MEDIA_HDD_PATH"),
    ({"services": {"app": {}}}, {"app": "app"}, {**V2_ENV, "MEDIA_HDD_PATH": "/run"}, V2_ROOTS, "$.site_env.MEDIA_HDD_PATH"),
    ({"services": {"app": {}}}, {"app": "app"}, {**V2_ENV, "MEDIA_HDD_PATH": "/srv"}, V2_ROOTS, "$.site_env.MEDIA_HDD_PATH"),
    ({"services": {"app": {}}}, {"app": "app"}, {**V2_ENV, "MEDIA_HDD_PATH": "relative"}, V2_ROOTS, "$.site_env.MEDIA_HDD_PATH"),
    ({"services": {"app": {}}}, {"app": "app"}, {key: value for key, value in V2_ENV.items() if key != "MEDIA_HDD_PATH"}, V2_ROOTS, "$.site_env.MEDIA_HDD_PATH"),
    ({"services": {"app": {}}}, {}, V2_ENV, V2_ROOTS, "$.services.app.tier"),
    ({"services": {"app": {}}, "secrets": {"X": {"file": "/tmp/X"}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "$.secrets.X"),
    ({"services": {"app": {"privileged": True}}}, {"app": "app"}, V2_ENV, V2_ROOTS, "$.services.app.privileged"),
])
def test_check_policy_rejects_rendered_model(model, tiers, env, roots, path):
    with pytest.raises(ValidationErrors) as exc:
        check_policy(model, tiers=tiers, site_env=env, bind_root_keys=roots)
    assert [error.path for error in exc.value.errors] == [path]


def test_check_policy_checks_literal_root_before_symlink_resolution(monkeypatch):
    realpath = os.path.realpath
    monkeypatch.setattr(os.path, "realpath", lambda path: "/srv/homelab/data" if path == "/srv" else realpath(path))
    with pytest.raises(ValidationErrors, match="protected path"):
        check_policy({"services": {}}, tiers={}, site_env={"APP_DATA_DIR": "/srv"}, bind_root_keys=["APP_DATA_DIR"])


def test_check_policy_rejects_data_root_resolving_into_vpn(monkeypatch):
    realpath = os.path.realpath
    monkeypatch.setattr(
        os.path,
        "realpath",
        lambda path: "/srv/vpn" if path == "/srv/homelab/data/vpn-link" else realpath(path),
    )
    with pytest.raises(ValidationErrors, match="/srv/vpn"):
        check_policy(
            {"services": {}},
            tiers={},
            site_env={"APP_DATA_DIR": "/srv/homelab/data/vpn-link"},
            bind_root_keys=["APP_DATA_DIR"],
        )


def test_check_policy_reports_every_violation():
    model = {"services": {"app": {"privileged": True, "runtime": "kata"}}}
    with pytest.raises(ValidationErrors) as exc:
        check_policy(model, tiers={"app": "app"}, site_env=V2_ENV, bind_root_keys=V2_ROOTS)
    assert {error.path for error in exc.value.errors} == {"$.services.app.privileged", "$.services.app.runtime"}
