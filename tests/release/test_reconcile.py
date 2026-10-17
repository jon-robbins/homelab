import shutil
from pathlib import Path

import pytest

from scripts.release import reconcile


ROOT = Path(__file__).parents[2]


class Client:
    def __init__(self, site):
        self.site = site
        self.plugin_sha = "a" * 40
        self.fetches = []

    def read(self, repository, _sha, path):
        if repository == "site" and path == "site.yaml":
            return (self.site / path).read_bytes()
        return None

    def ref(self, repository, _branch):
        assert repository == "owner/plugin-hello"
        return self.plugin_sha

    def fetch(self, repository, sha, directory):
        self.fetches.append((repository, sha))
        source = self.site if repository == "site" else ROOT / "examples/plugin-hello"
        destination = Path(directory)
        shutil.copytree(source, destination)
        return destination


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI with Compose")
def test_reconcile_renders_fetched_plugin_and_records_source(tmp_path):
    site = tmp_path / "site"
    shutil.copytree(ROOT / "examples/site", site)
    document = (site / "site.yaml").read_text().replace("{path: ../plugin-hello}", "{repository: owner/plugin-hello}")
    (site / "site.yaml").write_text(document)
    client = Client(site)
    sources, rendered = reconcile.render_release(client, ROOT, "site", "b" * 40, "c" * 40, tmp_path / "out" / "bundle.tar")
    assert sources == {"homelab": "c" * 40, "homelab-site": "b" * 40, "plugin-hello": "a" * 40}
    assert client.fetches == [("site", "b" * 40), ("owner/plugin-hello", "a" * 40)]
    assert "whoami" in rendered.app["services"]
    # compile runs later, once the built image digests are known; it reads this copy of site.yaml
    assert (tmp_path / "out" / "site.yaml").read_text() == document