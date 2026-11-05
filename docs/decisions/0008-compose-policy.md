# 0008: Compose policy v2 and inlined config files

- **Status:** Accepted (2026-10-15); the NET_BIND_SERVICE paragraph and the Caddyfile example superseded by
  [0020](0020-traefik-edge.md) (2026-11-02)
- **Code:** `scripts/release/compose_policy.py`, `scripts/release/render.py`, `config/release/cap-allowlist.json`,
  `config/release/gpu-allowlist.json`
- **Related:** [0001](0001-compose-modules.md), [0006](0006-host-layout.md), [security](../security.md)

## Decision

The release renders every module with stock `docker compose config` and then rejects the bundle unless:

- every bind is below a root named in `site.yaml` `bind_roots`, and no declared root overlaps a protected path
  (`/srv/homelab` except `data/`, unmanaged service directories, the Docker socket);
- `cap_add` uses only capabilities from `cap-allowlist.json`;
- GPU access is only from `gpu-allowlist.json` (`/dev/dri` devices, NVIDIA device requests);
- host networking is used only in the edge tier;
- no service is privileged and none mounts the Docker socket;
- secrets come only from `${HOMELAB_SECRET_DIR}/NAME` files.

Files a module needs from a repo (the original Caddyfile, scripts) are Compose `configs` whose content the
renderer inlines, so a release never points back into a git checkout. Binds are only for data and media.

NET_BIND_SERVICE is permitted for the pinned Caddy executable, which carries that file capability. Its module
retains it alongside `cap_drop: [ALL]` so a non-root process can execute it. This does not publish additional ports.

*Superseded by [0020](0020-traefik-edge.md) (2026-11-02): the Caddy module and this exception were removed with the
retirement; Traefik's listeners sit on unprivileged container ports and `cap-allowlist.json` no longer lists
`NET_BIND_SERVICE`. The repo files inlined as `configs` are now Traefik YAML, not a Caddyfile.*

## Consequences

Policy runs on the rendered model, after overrides are merged, so a site override cannot slip past it.

## Don't

- Don't widen an allowlist inside an unrelated change; each widening is its own reviewed commit.
- Don't bind-mount a file from the repo; use `configs`.
- Published ports on all interfaces require a site exposure declaration, enforced by
  [0019](0019-shared-media-mount.md) under [0015](0015-exposure-policy.md).
