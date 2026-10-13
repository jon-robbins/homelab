# Linux environment for the local gate, matching hosted CI (Ubuntu, Python 3.12).
# Tools the gate's tests call that GitHub's Ubuntu runners preinstall: jq,
# ripgrep, and shellcheck (actionlint checks run: scripts only when it is present).
# Built locally by scripts/dev/gate; not published.
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
COPY --from=ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 /uv /uvx /usr/local/bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates jq ripgrep shellcheck \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
ENV UV_CACHE_DIR=/cache/uv PRE_COMMIT_HOME=/cache/pre-commit UV_LINK_MODE=copy
