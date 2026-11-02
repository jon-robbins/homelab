import stat

import pytest

from setup.engine.secrets import SecretHandle, SecretStore


def store(tmp_path):
    return SecretStore(tmp_path / "secrets")


def test_ensure_generates_once_and_reuses_on_rerun(tmp_path):
    secrets = store(tmp_path)
    value = secrets.read(secrets.ensure("AUTHENTIK_PG_PASSWORD"))
    assert len(value) >= 32
    assert secrets.read(secrets.ensure("AUTHENTIK_PG_PASSWORD")) == value


def test_files_are_container_readable_inside_a_private_directory(tmp_path):
    secrets = store(tmp_path)
    handle = secrets.ensure("KEY")
    assert stat.S_IMODE(secrets.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(secrets.path(handle).stat().st_mode) == 0o444


def test_handle_never_shows_the_value(tmp_path):
    secrets = store(tmp_path)
    handle = secrets.ensure("KEY")
    value = secrets.read(handle)
    assert value not in repr(handle) + str(handle)
    assert isinstance(handle, SecretHandle)


@pytest.mark.parametrize("name", ["", "../x", "a/b", ".hidden", "with space", "x" * 200])
def test_unsafe_names_are_refused(tmp_path, name):
    with pytest.raises(ValueError):
        store(tmp_path).handle(name)


def test_empty_value_counts_as_missing_and_is_refused(tmp_path):
    secrets = store(tmp_path)
    with pytest.raises(ValueError):
        secrets.put("KEY", "")
    secrets.directory.mkdir(parents=True)
    (secrets.directory / "KEY").write_text("")
    assert not secrets.exists("KEY")
    assert secrets.read(secrets.ensure("KEY"))


def test_put_replaces_atomically_and_redact_hides_every_known_value(tmp_path):
    secrets = store(tmp_path)
    handle = secrets.put("TOKEN", "super-secret-token-value")
    secrets.put("TOKEN", "another-secret-token-value")
    assert secrets.read(handle) == "another-secret-token-value"
    text = "failed with another-secret-token-value in header"
    assert "another-secret-token-value" not in secrets.redact(text)
    assert secrets.redact("nothing here") == "nothing here"


def test_reading_a_missing_secret_is_an_error_not_an_empty_string(tmp_path):
    secrets = store(tmp_path)
    with pytest.raises(KeyError):
        secrets.read(secrets.handle("MISSING"))
