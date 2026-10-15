# Development

**Status: Current.** These commands exercise code already in this repository;
they do not publish or deploy it.

Run the fast checks and all non-Docker suites from the repository root:

```bash
scripts/dev/gate
```

It runs `pre-commit run --all-files` and `nox -t no-docker`: directly on Linux,
and in a pinned Linux container (`scripts/dev/gate.Dockerfile`) on macOS, so
results match CI. Extra arguments go to nox, e.g. `scripts/dev/gate -s release`.
`nox -t docker` runs the sessions that need Docker (Compose, docs).

For focused work, run `python3 -m pytest -q -p no:cacheprovider tests/boundary`
and the relevant package tests. Worker code lives in
[`apps/homelab-workers/`](../apps/homelab-workers/); MCP code and its extension
interface live in [`apps/homelab-mcp/`](../apps/homelab-mcp/). Media-agent
actions are registered under [`apps/media-agent/app/actions/`](../apps/media-agent/app/actions/).

A private extension should add its own package and tests outside this public
checkout, then connect through the documented MCP extension contract. The host
controller is **Planned**.
