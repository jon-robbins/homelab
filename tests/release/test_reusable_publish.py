import os
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]


def _publish_script(destination: str, release_sha: str, manifest_digest: str) -> str:
    workflow = yaml.safe_load((ROOT / ".github/workflows/reusable-publish.yml").read_text())
    script = workflow["jobs"]["publish"]["steps"][-1]["run"]
    replacements = {
        "${{ inputs.destination }}": destination,
        "${{ inputs.release-sha }}": release_sha,
        "${{ inputs.manifest-digest }}": manifest_digest,
        "${{ github.token }}": "test-token",
    }
    for source, value in replacements.items():
        script = script.replace(source, value)
    return script


def _fake_tool(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + body)
    path.chmod(0o755)


def test_publish_verifies_with_gh_and_rejects_promoted_digest_mismatch(tmp_path):
    log = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_tool(bin_dir / "gh", 'printf "gh %s\\n" "$*" >> "$CALL_LOG"\n')
    _fake_tool(
        bin_dir / "skopeo",
        'printf "skopeo %s\\n" "$*" >> "$CALL_LOG"\n'
        'if [ "$1" = inspect ]; then printf "%s\\n" "$INSPECT_DIGEST"; fi\n',
    )
    (tmp_path / "artifact/oci-layout.tar").parent.mkdir()
    (tmp_path / "artifact/oci-layout.tar").write_bytes(b"archive")
    expected = "sha256:" + "a" * 64
    script = _publish_script("ghcr.io/acme/homelab/web", "b" * 40, expected)
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALL_LOG": str(log),
             "INSPECT_DIGEST": "sha256:" + "c" * 64, "GITHUB_TOKEN": "test-token"},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    calls = log.read_text().splitlines()
    assert calls[0].startswith("gh attestation verify artifact/oci-layout.tar")
    assert "--signer-workflow jon-robbins/homelab/.github/workflows/post-merge-build.yml" in calls[0]
    assert "--source-digest " + "b" * 40 in calls[0]
    assert any(line.startswith("skopeo copy --preserve-digests") for line in calls)
    assert any(line.startswith("skopeo inspect --creds") for line in calls)
