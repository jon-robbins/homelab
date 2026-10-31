# Modules

A module is one ordinary Compose service in `modules/<tier>/<service>.compose.yml`, where `<tier>` is `app` or `edge`
and the service name matches the file name. There is no second module format: Compose expresses the service, and
`homelab.*` labels carry the homelab contract.

The rules:

1. A file contains exactly one service, named after the file, in the `app` or `edge` tier.
2. Its image is pinned by digest: `repository:tag@sha256:<64 hex>`. The tag is optional but keeps the pin readable
   and lets Dependabot propose bumps. Upstream images come from a namespace in `config/release/image-policy.json`;
   first-party images (`ghcr.io/jon-robbins/homelab/...`) are re-pinned by the release to the digest it built.
3. Every host bind is below one of the roots the site names in `bind_roots` (values from `site.env`). Files from a
   repository (`config/...`, the site's `files/...` and `edge/...`) are Compose `configs` with a `file:` source
   (`site:<path>` for the site repo); the renderer inlines their content and mode, so a release never points back
   into a git checkout. Secrets are `NAME: {file: ${HOMELAB_SECRET_DIR}/NAME}`, rendered by the Infisical agent on
   the host and never inlined.
4. The service has an enabled `healthcheck`, or, for images without a shell, a `homelab.probe` label such as
   `http://:8080/health` that documents the check.
5. `homelab.state` is exactly `stateful` or `stateless`. Stateful binds are snapshotted before changes and never
   restored automatically. `homelab-backup` snapshots a stateful service as `${APP_DATA_DIR}/<service>`, so a
   stateful service's binds under `APP_DATA_DIR` must sit under that directory (e.g.
   `${APP_DATA_DIR}/qbittorrent/config`).

Variables are only `${NAME}`, with `NAME` defined in the site's `site.env` (no `${NAME:-default}`, no `$NAME`).

Modules are a template for any homelab, so they never encode one site's disk layout. Media lives under one
directory, `MEDIA_DIR` in `site.env` (a bind root, and normally a marker root), with `downloads/`, `tv/`, `movies/`
and `music/` below it ([0019](decisions/0019-shared-media-mount.md)). The apps that import (Sonarr, Radarr, Lidarr,
Bazarr) mount all of it as `/data`, so a download and its library copy are on one mount and an import is a hardlink.
Jellyfin and Plex mount `/data` read-only; Navidrome mounts `music/` read-only; qBittorrent, slskd and Soularr mount
only `downloads/`, at `/data/downloads`. Apps are configured with `/data/tv`, `/data/movies`, `/data/downloads`.

A site with media on several disks can't hardlink across them and keeps its own paths. It points `MEDIA_DIR` at an
empty directory and restores the per-role mounts it wants (`MOVIES_DIR`, `TV_DIR`, `MUSIC_DIR`, `DOWNLOADS_DIR`) in
`overrides/<service>.yml`: an override entry with the same target replaces the module's. Anything specific to one
site's workflow (a seeding folder, Plex's symlink tree) belongs in the same place.

Existing split-disk sites must complete the [migration prerequisites](module-migration.md) before adopting this catalog.

Published ports bind an address from `site.env`: `MEDIA_BIND_IP` for Jellyfin's and Plex's direct ports,
`PEER_BIND_IP` for the torrent and Soulseek peer ports, `TAILSCALE_IP` for Caddy. A port that listens on every
interface (no address, `0.0.0.0` or `::`) fails policy unless the site lists it under `exposed` in `site.yaml`, as
`service:port/proto`. Overrides can add ports but not replace them, so a module publishes only what every site wants.

GPU access isn't in any module. A site that has a GPU adds it in `overrides/<service>.yml` (`gpus: all` for NVIDIA,
`devices: ["/dev/dri:/dev/dri"]` and `group_add` for Intel and AMD); policy checks it as usual. Compose rejects a
`gpus` setting in both a module and an override, which is why the module can't carry one.

Where each rule runs: `scripts/release/modules.py` checks the file layout and variables (what Compose would
erase); `scripts/release/compose_policy.py` checks everything else on the rendered model, after
`docker compose config` has merged overrides and normalized the syntax. To render a site and see the result:

```bash
python3 -m scripts.release.render examples/site      # from the repository root
```

Other Compose features remain ordinary Compose. `depends_on` is validated by Compose itself; applies select
services explicitly with `--no-deps`.
