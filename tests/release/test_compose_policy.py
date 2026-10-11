import json
from pathlib import Path

import pytest

from scripts.release.compose_policy import validate_compose_model
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
