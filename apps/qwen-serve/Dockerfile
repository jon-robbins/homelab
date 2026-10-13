# Reproduction image of the patched ~/qwen vLLM environment:
# vllm 0.27.1 + 8 vendored patches + KVarN KV-cache port.
# Spec: docs/superpowers/specs/2026-08-19-qwen-vllm-compose-design.md
FROM python:3.14-slim

# patch: vendored .patch files at build time; libgomp1: torch wheels;
# ca-certificates: pip over TLS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends patch libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir -r /tmp/requirements.lock.txt

# Apply the 8 vLLM patches exactly as ~/qwen/setup.sh does
# (patch -p1 -N -d <site-packages>/vllm). A failed apply aborts the build.
COPY patches/ /tmp/patches/
RUN set -e; \
    site="$(python -c 'import vllm, pathlib; print(pathlib.Path(vllm.__file__).parent)')"; \
    for p in /tmp/patches/*.patch; do \
        echo "applying $p"; \
        patch -p1 -N -d "$site" < "$p"; \
    done

# KVarN install — replicates ~/qwen/kvarn/install.sh with image-adjusted
# paths: copy modules, forward-apply the patch, clean pycache, then verify
# the kvarn_k4v2_g128 cache dtype and backend actually registered.
# The source installer tolerates patch failure (`|| true`) because it must
# be re-runnable on an already-patched venv; here the venv is freshly
# pip-installed in this same image, so the patch MUST apply cleanly — a
# failure aborts the build, and the python asserts below verify registration.
COPY kvarn/ /tmp/kvarn/
RUN set -e; \
    site="$(python -c 'import vllm, pathlib; print(pathlib.Path(vllm.__file__).parent)')"; \
    cp -r /tmp/kvarn/files/vllm/. "$site/"; \
    patch -p1 -N -r /dev/null -d "$site" < /tmp/kvarn/kvarn-0.27.1.patch; \
    find "$site" -type d -name __pycache__ -path "*kvarn*" -prune -exec rm -rf {} + 2>/dev/null || true; \
    python -c 'from typing import get_args; from vllm.config.cache import CacheDType; assert "kvarn_k4v2_g128" in get_args(CacheDType)'; \
    python -c 'from vllm.v1.attention.backends.registry import AttentionBackendEnum; print("KVarN backend:", AttentionBackendEnum.KVARN.get_class().get_name())'

# gcc/g++/make: torch inductor + triton compile the +rms_norm/+silu_and_mul
# custom ops at engine startup — a C compiler must exist at runtime
# (python:3.12-slim ships none; the host-side ~/qwen venv used system gcc).
# Installed AFTER the pip layer so image rebuilds reuse the cached wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 18020
ENTRYPOINT ["/entrypoint.sh"]
