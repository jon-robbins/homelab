# Linux environment for the local gate, matching hosted CI (Ubuntu, Python 3.12).
# Tools the gate's tests call that GitHub's Ubuntu runners preinstall: jq,
# ripgrep, shellcheck (actionlint checks run: scripts only when it is present),
# and the Docker CLI with Compose and Buildx, which talk to the host's daemon.
# Built locally by scripts/dev/gate; not published.
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
COPY --from=docker:28.5-cli@sha256:625d9431a9f54c5a2bc90f24f0e1c3d55b1349fd857dd85035f98c2c9acbdd4d /usr/local/bin/docker /usr/local/bin/
COPY --from=docker:28.5-cli@sha256:625d9431a9f54c5a2bc90f24f0e1c3d55b1349fd857dd85035f98c2c9acbdd4d /usr/local/libexec/docker/cli-plugins /usr/local/libexec/docker/cli-plugins
COPY --from=ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 /uv /uvx /usr/local/bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates jq ripgrep shellcheck \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
ENV UV_CACHE_DIR=/cache/uv PRE_COMMIT_HOME=/cache/pre-commit UV_LINK_MODE=copy
