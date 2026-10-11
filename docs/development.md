# Development

**Status: Current.** These commands exercise code already in this repository;
they do not publish or deploy it.

Run the public path from the repository root:

```bash
bash scripts/ci/public-check.sh
```

For focused work, run `python3 -m pytest -q -p no:cacheprovider tests/boundary`
and the relevant package tests. Worker code lives in
[`apps/homelab-workers/`](../apps/homelab-workers/); MCP code and its extension
interface live in [`apps/homelab-mcp/`](../apps/homelab-mcp/). Media-agent
actions are registered under [`apps/media-agent/app/actions/`](../apps/media-agent/app/actions/).

A private extension should add its own package and tests outside this public
checkout, then connect through the documented MCP extension contract. A signed
release feed and host controller are **Planned**.
