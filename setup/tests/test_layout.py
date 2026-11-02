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


def test_the_real_owner_root_is_checked_whatever_root_was_asked_for(tmp_path):
    owner = tmp_path / "owner"
    owner_host(owner)
    layout = Layout(tmp_path / "elsewhere", owner_root=owner, unit_dir=tmp_path / "units")
    with pytest.raises(HostClaimed, match="signed-release"):
        layout.claim()
    assert not (tmp_path / "elsewhere").exists()


@pytest.mark.parametrize(
    "leftover",
    ["tooling/current", "tooling/abc123/host", "units/homelab-controller.timer"],
)
def test_a_half_installed_owner_host_is_refused(tmp_path, leftover):
    owner = tmp_path / "owner"
    path = owner / leftover if not leftover.startswith("units/") else tmp_path / leftover
    path.parent.mkdir(parents=True)
    path.write_text("")
    with pytest.raises(HostClaimed, match="signed-release"):
        Layout(tmp_path / "srv", owner_root=owner, unit_dir=tmp_path / "units").refuse_owner_host()


def test_the_ownership_marker_is_a_flat_file_the_owner_installer_can_check(tmp_path):
    layout = Layout(tmp_path / "srv")
    assert layout.marker == tmp_path / "srv" / "local-setup.owner"


def test_alternate_root_claim_is_visible_to_the_owner_installer(tmp_path):
    layout = Layout(tmp_path / "alternate")
    layout.claim()
    assert (tmp_path / "owner/local-setup.owner").read_text().strip() == "local-setup"
