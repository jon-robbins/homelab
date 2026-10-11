import io
import os
import tarfile

import pytest

from scripts.release.bundle import build, bundle_sha256, verify


def make_source(root):
    for name in ("applications.json", "compose.yaml", "probe-targets.json"):
        (root / name).write_text(name, encoding="utf-8")
    (root / "files/nested").mkdir(parents=True)
    (root / "files/nested/config").write_text("config", encoding="utf-8")


def archive(*members):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as result:
        for member, content in members:
            if content is not None:
                member.size = len(content)
                result.addfile(member, io.BytesIO(content))
            else:
                result.addfile(member)
    return output.getvalue()


def regular(name, content=b"x"):
    member = tarfile.TarInfo(name)
    member.type = tarfile.REGTYPE
    return member, content


def directory(name):
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    return member, None


def valid_members():
    return [
        regular("applications.json"),
        regular("compose.yaml"),
        regular("probe-targets.json"),
        directory("files/"),
        directory("files/nested/"),
        regular("files/nested/config"),
    ]


def test_build_output_verifies(tmp_path):
    make_source(tmp_path)
    data = build(tmp_path)
    verify(data)
    assert bundle_sha256(data).startswith("sha256:")


def test_build_is_deterministic_across_mtimes_and_permissions(tmp_path):
    make_source(tmp_path)
    first = build(tmp_path)
    for path in tmp_path.rglob("*"):
        os.utime(path, (123456789, 123456789), follow_symlinks=False)
        if path.is_dir():
            path.chmod(0o700)
        else:
            path.chmod(0o600)
    second = build(tmp_path)
    assert first == second
    assert bundle_sha256(first) == bundle_sha256(second)


@pytest.mark.parametrize("bad_member", [
    pytest.param((lambda: (lambda m: (setattr(m, "type", tarfile.SYMTYPE) or (m, None)))(tarfile.TarInfo("files/link")))(), id="symlink"),
    pytest.param((lambda: (lambda m: (setattr(m, "type", tarfile.LNKTYPE) or setattr(m, "linkname", "files/target") or (m, None)))(tarfile.TarInfo("files/link")))(), id="hardlink"),
    pytest.param((lambda: (lambda m: (setattr(m, "type", tarfile.CHRTYPE) or setattr(m, "devmajor", 1) or setattr(m, "devminor", 3) or (m, None)))(tarfile.TarInfo("files/device")))(), id="character-device"),
    pytest.param((lambda: (lambda m: (setattr(m, "type", tarfile.FIFOTYPE) or (m, None)))(tarfile.TarInfo("files/fifo")))(), id="fifo"),
])
def test_verify_rejects_non_regular_members(bad_member):
    with pytest.raises(ValueError):
        verify(archive(*valid_members()[:4], bad_member))


@pytest.mark.parametrize("name", ["/absolute", "files/../escape", "files/x/../../escape"])
def test_verify_rejects_unsafe_paths(name):
    with pytest.raises(ValueError):
        verify(archive(*valid_members()[:4], regular(name)))


@pytest.mark.parametrize("members", [
    [*valid_members(), regular("extra")],
    [m for m in valid_members() if m[0].name != "applications.json"],
    [m for m in valid_members() if m[0].name != "files/"],
    [directory("applications.json"), *valid_members()[1:]],
    [*valid_members(), directory("gpu-inputs.json")],
    [*valid_members(), regular("applications.json/x")],
])
def test_verify_rejects_invalid_shape(members):
    with pytest.raises(ValueError):
        verify(archive(*members))


def test_verify_rejects_duplicate_member_name():
    members = valid_members()
    with pytest.raises(ValueError):
        verify(archive(*members, regular("compose.yaml")))


def test_verify_rejects_regular_and_directory_with_same_name():
    with pytest.raises(ValueError):
        verify(archive(*valid_members(), regular("files/a"), directory("files/a")))
