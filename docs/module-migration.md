# Adopting the shared-media catalog

This is a prerequisite for an existing split-disk site before adopting [0019](decisions/0019-shared-media-mount.md).
New sites use `MEDIA_DIR` directly. Nothing here moves data or changes application library settings.

The maintainer runs this against their own site. The public synthetic example is
[`tests/modules/owner_site`](../tests/modules/owner_site/), which preserves the old container paths, GPU access,
optional TMDB key and listeners. Its paths and listeners are examples, not values to copy blindly.

1. Deploy the platform contract extension that accepts `MEDIA_DIR`, `MEDIA_BIND_IP`, `PEER_BIND_IP` and `exposed`
   before changing the catalog. Commit `7746359` contains that extension without module changes.
2. In `site.env`, set `MEDIA_DIR` to an empty placeholder below an existing non-marker bind root. Do not use
   `APP_DATA_DIR`: stateful bind policy reserves each app's own subtree there. Keep the existing role paths and disk
   marker roots. Set `MEDIA_BIND_IP` and `PEER_BIND_IP` to the desired existing addresses; wildcard IPv4 uses
   `0.0.0.0`. Declare each wildcard port in `site.yaml` `exposed` as `service:published-port/protocol`.
3. Add overrides for every enabled affected service, preserving its old targets and read-only flags. The fixture
   shows Sonarr, Radarr, Bazarr, Lidarr, Jellyfin, Plex, qBittorrent, slskd, Soularr and Navidrome. Keep any existing
   seeding binds, Plex symlink trees, app environment and plugin mounts. slskd and Soularr need their old download
   paths in environment overrides too. Keep the owner's qBittorrent categories and save paths unchanged.
4. Restore any Caddy listener formerly supplied by the module (`80:80`, `8443:443`) in its override only if still
   needed; declare wildcard listeners in `exposed`. Keep existing tunnel loopback listeners. The module now supplies
   only tailnet HTTPS. Review the exposure list before accepting it.
5. If using a personal TMDB key, add the optional `TMDB_API_KEY` secret to the Seerr override as in the fixture.
   Keep the secret file; never put its value in the site or commands.
6. GPU overrides and GPU removal from the base module must land together. Preserve `gpus: all` for NVIDIA and
   the existing `/dev/dri` mapping and groups where used. Do not render the old GPU module with the new GPU override:
   Compose rejects the duplicate request.

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
if placeholder.exists() and any(placeholder.iterdir()):
    raise SystemExit("Choose an empty placeholder; do not point it at existing media")
placeholder.mkdir(parents=True, exist_ok=True)
(placeholder / "downloads").mkdir(exist_ok=True)
(placeholder / "music").mkdir(exist_ok=True)
PY
```

Render both versions into separate temporary directories before release. With the old catalog, omit only the new
GPU override fields. With the new catalog, include them. Compare every enabled service's mounts, environment,
secrets, devices, port bindings, health checks and resource settings. Only the empty `/data` or `/data/downloads`
placeholder binds and equivalent IPv4 wildcard notation are expected additions. Review the inlined Soularr script's
new fallback paths: an existing site must explicitly keep its old path through `SOULARR_DOWNLOAD_DIR`.

Verify the placeholder is accessible to the service UIDs, each real disk marker exists, existing libraries and download
queues still resolve to the same paths, and the GPU remains available before permitting owner rollout. The fixture
comparison cannot prove a private site's plugin and override behavior. Catalog changes recreate affected services;
edge changes use the separate edge updater. No restore is automatic.
