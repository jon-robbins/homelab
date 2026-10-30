"""Local image builds consume CI preparation entries without persistent source trees."""
import json
from pathlib import Path

import yaml

import noxfile


def test_local_images_prepare_before_docker_and_clean_temporary_context(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    matrix = yaml.safe_load((noxfile.ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["image-builds"]["strategy"]["matrix"]["include"]

    class Session:
        def __init__(self):
            self.commands = []
            self.prepared = None

        def install(self, *args):
            pass

        def run(self, *args, **kwargs):
            self.commands.append(args)
            if args[:2] == ("python", "-c"):
                return json.dumps(matrix)
            if args[0] == "python":
                assert args[1] == "config/seerr-oidc/build.py"
                assert args[-1] == "--prepare-only"
                self.prepared = Path(args[2])
                (self.prepared / "context").mkdir(parents=True)
            if args[:2] == ("docker", "build") and not self.prepared:
                assert "--platform" not in args
                assert "--build-arg" not in args
            if args[:2] == ("docker", "build") and self.prepared:
                assert args[args.index("--platform") + 1] == "linux/amd64"
                assert Path(args[-1]) == self.prepared / "context"
                assert Path(args[args.index("--file") + 1]) == self.prepared / "context/Dockerfile"
                assert Path(args[-1]).is_dir()
                lock = json.loads((noxfile.ROOT / "config/seerr-oidc/source.json").read_text())
                assert "COMMIT_TAG=" + lock["commit"] in args
                assert "org.opencontainers.image.revision=" + lock["commit"] in args
                assert "org.opencontainers.image.source=" + lock["repository"] in args
                assert "io.homelab.node-base=" + lock["node_image"] in args

    first = Session()
    noxfile.images(first)
    assert first.prepared is not None and not first.prepared.exists()
    assert len([args for args in first.commands if args[:2] == ("docker", "build")]) == 2
    assert len([args for args in first.commands if args[:2] == ("docker", "run")]) == 1
    second = Session()
    noxfile.images(second)
    assert second.prepared != first.prepared
    assert not second.prepared.exists()
