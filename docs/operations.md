# Operations

**Status: Current and planned.** Current CI validates source, renders Compose
with synthetic inputs, runs tests, scans the public boundary, and builds OCI
images off-host. It does not deploy or start containers.

The supported local equivalent is:

```bash
bash scripts/ci/public-check.sh
```

A signed release feed (N6) and a host controller for N7–N11 are **Planned**.
Until those exist, do not describe a release feed, automatic host pull, or
controller reconciliation as available behavior. Private site operations and
runtime backups belong outside this public repository.

