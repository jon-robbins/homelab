# 0019: One shared media mount, explicit ports, GPU in the site

- **Status:** Proposed (2026-10-31; implements the settled storage/modules work package, pending integration review)
- **Code:** `modules/`, `scripts/release/modules.py` (`MEDIA_DIR`, `MEDIA_BIND_IP`, `PEER_BIND_IP`, `exposed`),
  `scripts/release/compose_policy.py` (`_ports`)
- **Related:** [0001](0001-compose-modules.md), [0007](0007-disk-markers.md), [0008](0008-compose-policy.md),
  [0015](0015-exposure-policy.md), [0018](0018-local-setup-path.md), [modules guide](../modules.md), [migration prerequisites](../module-migration.md)

## Context

The catalog mounted `/downloads`, `/tv` and `/movies` as separate binds. Separate mounts can't be hardlinked across, even
on one disk, so every import was a copy: double the space, and torrents that stop seeding. It also published
ports on every interface, and put `gpus: all` in Jellyfin and Plex, which won't start on a host without the NVIDIA
runtime. Both suit one owner's machine and fail a stranger's.

## Decision

1. Media lives under one directory, `MEDIA_DIR`, with `downloads/`, `tv/`, `movies/`, `music/`. Importing apps mount it
   whole as `/data`; library readers mount it read-only; downloaders mount only `downloads/`, at `/data/downloads`.
   Apps are configured with paths under `/data`.
2. A site with media on several disks can't hardlink across them. It points `MEDIA_DIR` at an empty directory and restores
   its own mounts in overrides. The per-role keys stay in `site.env` for that, and for plugins.
3. Published ports use `MEDIA_BIND_IP`, `PEER_BIND_IP` or `TAILSCALE_IP`. Policy rejects a port on every interface
   unless `site.yaml` `exposed` lists it. Caddy's module publishes only its tailnet port.
4. No module declares a GPU. The site adds one in an override.
5. Seerr's module no longer requires `TMDB_API_KEY`.

## Consequences

- Sites adopt this in order: accept the keys (platform), prepare the site, then change the modules. A site override can't
  set `gpus` while a module does, so GPU is the one step that has to land together with the site's override.
- Every module whose mounts or ports changed is recreated at the next apply.

## Don't

- Don't give downloaders the whole `/data`, or library readers write access.
- Don't put a GPU, a port on every interface, or a site's disk layout back in a module.
- Don't hold a site to the module's paths by editing the module; the site's override restores its own.
