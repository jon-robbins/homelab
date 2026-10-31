import pytest

from setup.engine.layout import HostClaimed, Layout


def tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")) if root.exists() else []


def owner_host(root):
    (root / "trust").mkdir(parents=True)
    (root / "trust" / "policy.json").write_text("{}")


def test_owner_pipeline_host_is_refused_before_any_write(tmp_path):
    root = tmp_path / "srv"
    owner_host(root)
    before = tree(root)
    with pytest.raises(HostClaimed, match="signed-release"):
        Layout(root).claim()
    assert tree(root) == before


def test_constructing_a_layout_and_reading_status_write_nothing(tmp_path):
    root = tmp_path / "srv"
    layout = Layout(root)
    assert layout.mode() is None
    assert not root.exists()


def test_claim_writes_the_marker_and_is_repeatable(tmp_path):
    layout = Layout(tmp_path / "srv")
    layout.claim()
    layout.claim()
    assert layout.mode() == "local-setup"


def test_unknown_marker_is_refused_with_recovery_steps_and_left_alone(tmp_path):
    layout = Layout(tmp_path / "srv")
    layout.claim()
    layout.marker.write_text("something-else\n")
    with pytest.raises(HostClaimed) as error:
        layout.claim()
    assert str(layout.marker) in str(error.value)
    assert layout.marker.read_text() == "something-else\n"


def test_a_controller_that_already_ran_here_is_refused(tmp_path):
    root = tmp_path / "srv"
    (root / "releases").mkdir(parents=True)
    (root / "releases" / "current").symlink_to(root)
    with pytest.raises(HostClaimed):
        Layout(root).claim()
