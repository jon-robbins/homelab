#!/bin/sh
# Export the file-only secret as the env var vLLM expects, then exec the
# compose-provided command (vllm serve ...). Fail closed: vLLM treats an
# empty VLLM_API_KEY as "auth disabled", so a missing or empty secret file
# must abort the container instead of silently serving unauthenticated.
set -e
if [ ! -s /run/secrets/VLLM_API_KEY ]; then
    echo "FATAL: /run/secrets/VLLM_API_KEY is missing or empty — refusing to start (vLLM would run with auth disabled)" >&2
    exit 1
fi
VLLM_API_KEY="$(tr -d '\r\n' < /run/secrets/VLLM_API_KEY)"
export VLLM_API_KEY

# Preflight: every model file must be readable by THIS process. The service
# runs with cap_drop: [ALL], so even root cannot read mode-600 files owned
# by other uids — safetensors then fails deep in the loader with a
# misleading FileNotFoundError. Stage/refresh weights with mode 0644.
UNREADABLE="$(find /models -type f ! -readable -print 2>/dev/null)"
if [ -n "$UNREADABLE" ]; then
    COUNT="$(printf '%s\n' "$UNREADABLE" | wc -l)"
    echo "FATAL: $COUNT model file(s) under /models are not readable under cap_drop:ALL (expected mode 0644). First offenders:" >&2
    printf '%s\n' "$UNREADABLE" | head -5 >&2
    echo "Fix on the host: find <repo>/data/qwen/models -type f -exec chmod 0644 {} +" >&2
    exit 1
fi

exec "$@"
