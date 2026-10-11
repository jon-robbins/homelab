# syntax=docker/dockerfile:1.6
FROM debian:bookworm-slim AS base
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl bash jq coreutils gosu python3 xz-utils procps \
  && rm -rf /var/lib/apt/lists/*

# Non-root user (uid/gid numeric stability)
RUN groupadd -g 10001 qwen && useradd -u 10001 -g 10001 -m -s /bin/bash qwen

# ---- Vendored installer pin (build-time SHA check) ----
WORKDIR /opt/vendor
COPY vendor/SHA256SUMS ./
COPY vendor/install-qwen-standalone-v0.21.15.sh ./
RUN sha256sum -c SHA256SUMS --strict || { echo "BUILD FAIL: vendored installer SHA mismatch"; exit 3; }
RUN chmod +x install-qwen-standalone-v0.21.15.sh

# ---- Install Qwen Code standalone as qwen user ----
USER qwen
WORKDIR /home/qwen
RUN bash /opt/vendor/install-qwen-standalone-v0.21.15.sh --no-modify-path --method standalone --version 0.21.15 \
  || { echo "BUILD FAIL: qwen standalone installer failed"; exit 4; }
ENV PATH="/home/qwen/.local/bin:${PATH}"
RUN qwen --version | grep -q 0.21.15 || { echo "BUILD FAIL: qwen version is not 0.21.15"; exit 5; }

# ---- System-level security policy (root-owned, immutable) ----
USER root
COPY files/etc/qwen-code/settings.json /etc/qwen-code/settings.json
RUN chown root:root /etc/qwen-code/settings.json && chmod 0644 /etc/qwen-code/settings.json

# ---- Entrypoint + workspace scaffold ----
COPY files/usr/local/bin/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

# ---- Runtime settings render helper & default policy ----
RUN mkdir -p /usr/local/lib/qwen-code /usr/local/share/qwen-code /run/qwen-code /workspace /data \
  && chown -R qwen:qwen /run/qwen-code /workspace /data /home/qwen
COPY scripts/render_project_settings.py /usr/local/lib/qwen-code/render_project_settings.py
COPY config/mcp-role-tools.json /usr/local/lib/qwen-code/mcp-role-tools.json
RUN chmod 0755 /usr/local/lib/qwen-code/render_project_settings.py
COPY files/usr/local/share/qwen-code/QWEN.md /usr/local/share/qwen-code/QWEN.md
RUN chmod 0644 /usr/local/share/qwen-code/QWEN.md

WORKDIR /workspace
VOLUME ["/workspace", "/data"]

ENV QWEN_CODE_SYSTEM_SETTINGS_PATH=/etc/qwen-code/settings.json

USER qwen:qwen
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
STOPSIGNAL SIGINT
# no EXPOSE, no HEALTHCHECK, no CMD.
