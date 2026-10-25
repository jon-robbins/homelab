# 0017: Personal services are plugins, not catalog modules

- **Status:** Accepted (2026-10-25)
- **Amends:** [0002](0002-public-platform-private-site.md) (what goes in a plugin repo, and what stays here)
- **Code:** `modules/`, `docs/modules.md`
- **Related:** [0002](0002-public-platform-private-site.md), [0009](0009-image-trust.md), [modules](../modules.md),
  [plugins guide](../plugins.md)

## Context

[0002](0002-public-platform-private-site.md) says plugin repos hold "services only one owner runs", and
[modules](../modules.md) says catalog modules never encode one site's disk layout or workflow. Four small services
built on a shared `homelab-workers` package nevertheless stayed in this repository, with their own first-party
image. They stayed by accident, not by decision: a private plugin pinned the package by commit, so it could not
move.

| Service | Does | Why it is personal |
|---|---|---|
| `homelab-qbt-events` | seed slots, exclusive seeding, freeleech batching | private-tracker ratio management |
| `homelab-arr-post-import` | relocates imports between two disks | a two-disk mapping that belongs to one site |
| `homelab-arr-missing-search` | periodic missing-episode and movie search | opinionated; stopped in the owner's site |
| `homelab-seerr-request-reconciler` | reconciles Seerr requests with the *arrs | opinionated; stopped in the owner's site |

## Decision

1. The platform has three layers:

   | Layer | Holds | Lives in |
   |---|---|---|
   | core | controller, renderer, Compose policy, host scripts, edge (caddy, cloudflared) | this repository |
   | catalog | services anyone running homelab could use: the *arr stack, qBittorrent, Seerr, Plex, Jellyfin, music | `modules/` in this repository |
   | personal | services shaped by one setup | a private plugin |

2. The catalog rule: a service belongs in `modules/` only if it works for a stranger without encoding a seeding
   folder, a disk mapping or a tracker policy. Everything else is a plugin.
3. The four services above moved to a private plugin, with their package name, service names and console-script
   names unchanged, so state directories, site overrides and health checks need no change.
4. This repository ships no worker image and no `homelab_workers` package. The only first-party image is caddy
   ([0009](0009-image-trust.md)).
5. A worker that is later generalized (missing-search is the likeliest) may come back as a catalog module.

## Consequences

- The cross-repository commit pin that tied the plugin to this repository is gone; the plugin's imports are
  in-repo.
- The plugin's image is unsigned by this repository's attestation flow. [0009](0009-image-trust.md) already
  accepts plugin images pinned by digest; the plugin build hardens itself instead.
- The catalog stays small, and its docs no longer describe services a new user would not run.
- A site that ran these services enables them from its plugin; nothing on the host moves.

## Don't

- Don't re-add a personal service to `modules/` because it is convenient to keep it next to the platform.
- Don't name a plugin repository from `modules/`, `scripts/`, `host/` or these docs ([0002](0002-public-platform-private-site.md)).
