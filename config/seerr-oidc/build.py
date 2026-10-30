#!/usr/bin/env python3
"""Build the explicitly approved Seerr OIDC exception; never publish or deploy it."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request


def prepare(archive: bytes, destination: Path, lock: dict) -> Path:
    """Verify upstream inputs and change only the two Node FROM references."""
    if hashlib.sha256(archive).hexdigest() != lock["archive_sha256"]:
        raise ValueError("source archive checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
        source.extractall(destination, filter="data")
    context = destination / ("seerr-" + lock["commit"])
    dockerfile = context / "Dockerfile"
    original = dockerfile.read_bytes()
    if hashlib.sha256(original).hexdigest() != lock["dockerfile_sha256"]:
        raise ValueError("upstream Dockerfile checksum mismatch")
    old = "FROM " + lock["original_node_image"]
    text = original.decode()
    if sum(line.startswith(old) for line in text.splitlines()) != 2:
        raise ValueError("expected exactly two upstream Node base references")
    dockerfile.write_text(text.replace(old, "FROM " + lock["node_image"]))
    return context


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="new, empty build directory")
    parser.add_argument("--tag", default="homelab-seerr-oidc:2e523125-node22.23.4")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=False)
    lock = json.loads(Path(__file__).with_name("source.json").read_text())
    with urllib.request.urlopen(lock["archive_url"], timeout=90) as response:
        archive = response.read()
    context = prepare(archive, args.destination, lock)
    print("Verified source", lock["commit"], "and upstream Dockerfile", flush=True)
    print("Node base:", lock["node_image"], flush=True)
    if args.prepare_only:
        return
    subprocess.run([
        "docker", "buildx", "build", "--load", "--platform", lock["platform"],
        "--build-arg", "COMMIT_TAG=" + lock["commit"],
        "--label", "org.opencontainers.image.source=" + lock["repository"],
        "--label", "org.opencontainers.image.revision=" + lock["commit"],
        "--label", "io.homelab.node-base=" + lock["node_image"],
        "--tag", args.tag, str(context),
    ], check=True)
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", args.tag]))[0]
    (args.destination / "build-result.json").write_text(json.dumps({
        "tag": args.tag, "image_id": image["Id"], "inputs": lock,
    }, indent=2) + "\n")
    print("Built", args.tag, image["Id"])


if __name__ == "__main__":
    main()
