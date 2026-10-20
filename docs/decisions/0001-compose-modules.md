# 0001: Services are plain Compose files with `homelab.*` labels

- **Status:** Accepted (2026-10-15)
- **Code:** `modules/`, `scripts/release/modules.py`, `scripts/release/compose_policy.py`
- **Related:** [0008](0008-compose-policy.md), [modules guide](../modules.md)

## Context

The first version of this homelab described services three times: a custom `manifest.yaml`, a `compose.json`
and a `probes.json`, plus `x-homelab` extension keys. They drifted apart, every change touched several files,
and nothing outside the project could read them.

## Decision

A module is one ordinary Compose file, `modules/<tier>/<service>.compose.yml`, holding exactly one service.
Facts Compose cannot express are `homelab.*` labels, which also appear in `docker inspect` on the running
container. Five rules apply: one service per file named after the file, a digest-pinned image, binds only under
declared roots, a health check, and `homelab.state: stateful | stateless`.

The file suffix is `.compose.yml` because Dependabot only reads Compose files with that name.

## Consequences

- Any release directory is a plain Compose project; `docker compose` can run it with no homelab tooling.
- `depends_on` is plain Compose and is used for validation only; applies always pass `--no-deps`.
- There is no schema to version. New metadata means a new label and a rule in `modules.py`.

## Don't

- Don't add a second description format (YAML manifest, JSON schema, `x-homelab` keys) next to Compose.
- Don't put more than one service in a module file.
- Don't use `${NAME:-default}`: a module is a template, and defaults hide missing site values.
