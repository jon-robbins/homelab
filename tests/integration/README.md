# Integration test scripts

These scripts hit a live homelab stack (Docker Compose). They are not run in default PR CI; use them on a host where the stack is up.

## Media pipeline E2E — retired

The Overseerr → Arr → qBittorrent live E2E runner (`tests/integration/media-pipeline-e2e.sh`, `media-pipeline-e2e` console script) is **removed**. Do not restore it as a supported CI or nightly gate.
