# Adopting the shared-media catalog

This is a prerequisite for an existing split-disk site before adopting [0019](decisions/0019-shared-media-mount.md).
New sites use `MEDIA_DIR` directly. Nothing here moves data or changes application library settings.

The maintainer runs this against their own site. The public synthetic example is
[`tests/modules/owner_site`](../tests/modules/owner_site/), which preserves the old container paths, GPU access,
optional TMDB key and listeners. Its paths and listeners are examples, not values to copy blindly.

Use four separately reviewed changes in this order. A merge can trigger a release, so rendering only the final
combination is insufficient. Keep baseline and candidate site copies outside the public repository.

1. **Platform stage 1:** merge the contract extension (commit `7746359`, PR #95), which accepts `MEDIA_DIR`,
   `MEDIA_BIND_IP`, `PEER_BIND_IP` and `exposed` without changing modules. Confirm the PR is merged before proceeding.
2. **Site PR 1:** add the new environment keys, exposures and compatibility overrides below, with **no new GPU
   fields**. Render against stage 1 and require an empty comparison against the original site. Merge only after
   this check. This site must work with both catalogs.
3. **Host preparation:** create and verify the placeholder directories with the script below. Check permissions for
   the service UIDs and retain all existing disk markers. This is an operator action, separate from merging code.
4. **Site PR 2, then platform stage 2 immediately:** move GPU fields to site overrides and land the module change
   (commit `f6d9fbf` plus the reviewed Caddy fix). Pre-render this pair before either merge. The intermediate site
   with GPU overrides and old modules can fail rendering due to duplicate GPU requests; never apply it. A failed
   render must produce no release. Verify that final signed release names both intended commits before applying it.
   If stage 2 cannot merge immediately, revert site PR 2 and keep stage 1 with site PR 1. After a stage 2 rollback,
   restore the matching site revision too; do not leave GPU overrides with the old modules.

The site PR 1 patch must include:

- In `site.env`, set `MEDIA_DIR` to an empty placeholder below an existing non-marker bind root. Do not use
  `APP_DATA_DIR`: stateful bind policy reserves each app's own subtree there. Keep the existing role paths and disk
  marker roots. Set `MEDIA_BIND_IP` and `PEER_BIND_IP` to the existing addresses; wildcard IPv4 uses `0.0.0.0`.
  Declare each wildcard port in `site.yaml` `exposed` as `service:published-port/protocol`.
- Restore old mount targets and read-only flags in `overrides/<service>.yml`. The fixture shows Sonarr, Radarr,
  Bazarr, Lidarr, Jellyfin, Plex, qBittorrent, slskd, Soularr and Navidrome. Preserve existing seeding binds, Plex
  symlink trees, app environment and plugin mounts. slskd and Soularr also need their old download environment paths.
  Keep qBittorrent categories and save paths unchanged.
- Preserve needed Caddy listeners (`80:80`, `8443:443`) in its override, plus existing tunnel loopback listeners.
  Declare wildcard listeners in `exposed`. Compose deduplicates identical ports with the old catalog; verify the
  unchanged render rather than assuming that. The new module supplies only tailnet HTTPS.
- If using a personal TMDB key, add the optional `TMDB_API_KEY` secret to the Seerr override as in the fixture.
  Keep its file; never put its value in the site or commands.

Site PR 2 adds only the GPU fields removed from modules: `gpus: all` for NVIDIA and existing `/dev/dri` devices and
supplementary groups where used. Preserve any GPU fields already supplied by the site rather than duplicating them.
The fixture shows the final site; omit its newly moved GPU fields when constructing site PR 1.

Caddy also needs `cap_add: [NET_BIND_SERVICE]` with the pinned image, even as a non-root user: the executable has a
file capability and fails to execute when Docker drops it from the bounding set. The catalog fix and capability
allowlist widening need their own reviewed commit under [0008](decisions/0008-compose-policy.md). If the site already
adds it, Compose must retain one equivalent capability. This is an expected render addition for a site missing it.

Run this preparation on the target host only after reviewing the site's settings. It creates empty placeholder
folders; it does not start services. `SITE_DIR` is the path to the operator's site checkout. The script reads values
without sourcing `site.env` as shell code:

```bash
python3 - "$SITE_DIR" <<'PY'
from pathlib import Path
import sys
from scripts.release.modules import load_site

site = load_site(Path(sys.argv[1]), Path.cwd())
placeholder = Path(site.env["MEDIA_DIR"])
if not placeholder.is_absolute():
    raise SystemExit("MEDIA_DIR must be absolute")
expected = {"downloads", "music"}
if placeholder.is_symlink() or (placeholder.exists() and not placeholder.is_dir()):
    raise SystemExit("Placeholder must be a directory, not a symlink")
if placeholder.exists():
    for child in placeholder.iterdir():
        if child.name not in expected or child.is_symlink() or not child.is_dir() or any(child.iterdir()):
            raise SystemExit("Placeholder may contain only empty downloads/ and music/ directories")
placeholder.mkdir(parents=True, exist_ok=True)
(placeholder / "downloads").mkdir(exist_ok=True)
(placeholder / "music").mkdir(exist_ok=True)
PY
```

Run the comparison from this platform checkout. All paths below are operator-supplied external checkouts. Set
`OLD_PLATFORM` to stage 1, `OLD_SITE` to the original site, `PREPARED_SITE` to site PR 1 and `GPU_SITE` to site PR 2.
`NEW_PLATFORM` is the proposed stage 2 platform. Supply the same `--plugin NAME=DIR` arguments to both invocations
when needed. Keep output private: it includes site paths, addresses and environment settings.

```bash
python3 -m scripts.release.compare "$OLD_PLATFORM" "$OLD_SITE" "$OLD_PLATFORM" "$PREPARED_SITE" > "$PRIVATE_STAGE1_REPORT" 2>&1
python3 -m scripts.release.compare "$OLD_PLATFORM" "$PREPARED_SITE" "$NEW_PLATFORM" "$GPU_SITE" > "$PRIVATE_STAGE2_REPORT" 2>&1
```

Exit 2 means render or policy failure. Exit 1 means a service or protected setting was removed; stop and investigate.
Exit 0 still requires reviewing **every** reported difference. Stage 1 must report `{}`. Stage 2 should add only empty
placeholder mounts and Caddy's capability where previously absent, plus the reviewed Soularr config digest change.
The comparison ignores port and mount ordering and keeps an omitted host address distinct from explicit `0.0.0.0`: Docker may publish an omitted address on both IPv4 and IPv6, while the explicit address selects IPv4. Treat this transition as a listener loss requiring review. It reports other
changed settings, including environment, health checks and resource settings; a changed `deploy.resources` is
conservatively a loss requiring review. It does not prove that application paths, actual permissions or a GPU work.
Review the inlined Soularr script's fallback paths: the site must keep its old path through `SOULARR_DOWNLOAD_DIR`.

Verify the placeholder is accessible to the service UIDs, each real disk marker exists, existing libraries and download
queues still resolve to the same paths, and the GPU remains available before permitting owner rollout. The fixture
comparison cannot prove a private site's plugin and override behavior. Catalog changes recreate affected services;
edge changes use the separate edge updater. No restore is automatic.
