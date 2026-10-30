# Seerr OIDC exception build

Approved on 2026-10-30 to test upstream OIDC before its stable release. This recipe
feeds credential-free PR and post-merge builds. Post-merge OCI artifacts receive
GitHub build provenance and are published through the normal signed-release path
to `ghcr.io/jon-robbins/homelab/seerr-oidc`. Publication does not activate the service;
the service module remains on its existing image until migration is prepared.

`source.json` locks the upstream commit/archive, original Dockerfile checksum,
and the replacement Node image. `build.py` verifies those inputs, uses Python's
safe tar extraction, and changes only the two Node `FROM` references. Upstream's
frozen dependency lockfile and recipe otherwise remain unchanged. The supported
target is `linux/amd64`.

```sh
python3 config/seerr-oidc/build.py /tmp/seerr-oidc-build-new
python3 tests/images/test_seerr_oidc_build.py
```

Requires Python 3.12+ and Docker Buildx. A successful build writes
`build-result.json` below the requested build directory, containing the local
image identity and input pins. The directory must not already exist.
`--prepare-only` verifies and extracts the source into its `context/` subdirectory
without invoking Docker. CI and local `nox -s images` use this same preparation,
`linux/amd64` target, `COMMIT_TAG`, and OCI source/revision/Node labels from the lock.
The runtime `/api/v1/status` commit tag therefore identifies the pinned source.

The inputs are pinned, but the image is not guaranteed byte-identical across
build times: upstream's `apk add tzdata` still resolves from Alpine repositories.
The resulting image identity must be recorded and separately pinned when it is
published. Never treat the mutable local tag as a production release pin.

For maintenance, review the current upstream OIDC branch and stable security
changes, update the exact source/checksums and Node tag/digest together, rerun the
recipe tests and build, and repeat sanitized-copy migration and browser login
acceptance. Do not replace the source with the stale published preview tag.
