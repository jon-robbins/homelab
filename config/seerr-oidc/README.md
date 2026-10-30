# Seerr OIDC exception build

Approved on 2026-10-30 to test upstream OIDC before its stable release. This recipe
is a build/test tool; it does not publish images or change the service module.
Production still requires the normal signed-release path.

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

The inputs are pinned, but the image is not guaranteed byte-identical across
build times: upstream's `apk add tzdata` still resolves from Alpine repositories.
The resulting image identity must be recorded and separately pinned when it is
published. Never treat the mutable local tag as a production release pin.

For maintenance, review the current upstream OIDC branch and stable security
changes, update the exact source/checksums and Node tag/digest together, rerun the
recipe tests and build, and repeat sanitized-copy migration and browser login
acceptance. Do not replace the source with the stale published preview tag.
