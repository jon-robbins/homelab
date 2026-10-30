"""The custom build must reject changed upstream inputs and escaping archives."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("seerr_build", Path(__file__).parents[2] / "config/seerr-oidc/build.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def fixture(name=None):
    dockerfile = b"FROM node:old AS base\nRUN pnpm build\nFROM node:old\n"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo(name or "seerr-abc/Dockerfile")
        info.size = len(dockerfile)
        archive.addfile(info, io.BytesIO(dockerfile))
    data = stream.getvalue()
    lock = {"commit": "abc", "archive_sha256": hashlib.sha256(data).hexdigest(),
            "dockerfile_sha256": hashlib.sha256(dockerfile).hexdigest(),
            "original_node_image": "node:old", "node_image": "node:new@sha256:pinned"}
    return data, lock


class BuildTests(unittest.TestCase):
    def test_changes_only_both_node_bases(self):
        data, lock = fixture()
        with tempfile.TemporaryDirectory() as directory:
            context = build.prepare(data, Path(directory), lock)
            self.assertEqual(context, Path(directory) / "context")
            self.assertEqual((context / "Dockerfile").read_text(),
                             "FROM node:new@sha256:pinned AS base\nRUN pnpm build\nFROM node:new@sha256:pinned\n")

    def test_changed_archive_refused(self):
        data, lock = fixture()
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "archive checksum"):
            build.prepare(data + b"tampered", Path(directory), lock)

    def test_changed_dockerfile_refused(self):
        data, lock = fixture()
        lock["dockerfile_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "Dockerfile checksum"):
            build.prepare(data, Path(directory), lock)

    def test_archive_escape_refused(self):
        data, lock = fixture("../../escape")
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(tarfile.FilterError):
            build.prepare(data, Path(directory), lock)


if __name__ == "__main__":
    unittest.main()
