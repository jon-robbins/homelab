# 0018: Guided local setup for new homelabs

- **Status:** Accepted (2026-10-31)
- **Code:** `scripts/`, `modules/`, `config/ci/budgets.json`
- **Amends:** [0002](0002-public-platform-private-site.md) (the private site repo is the owner's pipeline; strangers use a generated local site directory)
- **Scopes:** [0004](0004-signed-full-state-releases.md) and the signed-release rule in `AGENTS.md` apply to owner-pipeline hosts
- **Related:** [0005](0005-slim-controller.md), [0010](0010-secrets.md), [0011](0011-complexity-budgets.md), [0012](0012-backups.md), [0015](0015-exposure-policy.md), [0017](0017-personal-services-are-plugins.md), [architecture](../architecture/overview.md)

## Context

The product is for a person with a spare Linux machine and a few free accounts who wants a working, secure media
server without becoming a systems administrator. Requiring a fork, a private site repository, and a deployment
pipeline makes the owner's operating model the stranger's setup guide. The owner still uses that pipeline, but it is
not the public product's setup path.

## Decision

1. A stranger starts from the public repository's guided local setup. It prepares one supported Linux host and creates
   a local site directory owned by that person. It does not require a fork, GitHub account, private site repository,
   or access to the owner's infrastructure.
2. A host has exactly one setup owner and mode: local setup or the owner's signed-release pipeline. Setup must detect
   and refuse a host already claimed by the other mode before making changes. Neither mode adopts, overwrites, or
   starts services owned by the other. The implementation must use a durable ownership marker and explain how to
   recover from a stale marker without silently switching modes.
3. The signed full-state release and host verification in [0004](0004-signed-full-state-releases.md) remain compulsory
   for owner-pipeline hosts. A locally set up host does not run the owner's controller or follow the feed. Updates are
   published as tagged, signed versions, and a person applies one with `homelab update`, which verifies it first. Nothing
   changes on their machine by itself. A signature proves a version came from this project's workflow; it does not
   protect against someone who controls that workflow. Both paths keep image digests pinned and preserve the security
   policy.
4. [0015](0015-exposure-policy.md) governs both paths. Internet exposure stays limited to explicitly allowlisted
   services; administrative interfaces remain private. Video streaming to family devices uses Tailscale. The setup
   path does not promise public video streaming, and devices without Tailscale support are outside the supported
   playback path.
5. [0012](0012-backups.md) continues to govern snapshots and restore behavior. Setup strongly prompts for an
   off-machine backup destination; a person may skip it after acknowledging a warning. Setup never restores data
   automatically. Setup requires acknowledging a notice that torrent peers see the home IP address and that downloading
   also uploads, and that Cloudflare and Tailscale change neither. A VPN module may come later as an add-on.
6. Keep the wizard engine independent of its terminal interface. The first interface is a TUI; the engine owns
   checkpoints and step state, and steps do not print or read the terminal. This leaves room for another interface
   without putting setup logic in the screens.
7. Keep [0017](0017-personal-services-are-plugins.md)'s plugin boundary. The local site directory contains generated
   user configuration, not code or personal services. Nothing in the public product depends on a private repository.
8. The product is community-supported, with no guarantees of support or service availability.
9. The setup wizard is a separate entity in a top-level `setup/` directory, holding its engine, screens, provider clients and
   app wiring, with its own dependencies and tests. The dependency runs one way: `setup/` may call the platform's
   published interfaces (the renderer, planner and policy), and nothing in `scripts/`, `host/` or `modules/` may import
   from or depend on `setup/`. CI enforces this. Deleting `setup/` must leave the owner's pipeline fully working. Code
   that both paths need moves into the platform, stays small and stays free of setup logic.
10. `setup/` has its own budget of **2,000 formatted lines**, recorded in [0011](0011-complexity-budgets.md) and
    `config/ci/budgets.json` when the directory is created. The controller, renderer and `host/` budgets don't change.

## Consequences

- A new user gets a guided setup without creating or publishing a repository. Their local site directory remains theirs
  to back up and protect.
- A stranger does not get the owner's automated controller path. They run setup and later updates themselves, with the
  trust and maintenance responsibilities that entails.
- Tailscale is required on the host and each playback device for remote video. Users who need public video access or
  devices that cannot join Tailscale need a different product path.
- This is a community project, not a managed service; users receive no support guarantee.

## Rejected

- **Make strangers use the owner's release feed:** it would give the public product a deployment dependency on the
  owner's private site configuration and would blur who approves changes on a user's host.
- **Allow both setup modes on one host:** it would create competing authorities over services and persistent data.
- **Send video through the public tunnel:** the design does not rely on the tunnel for video; Tailscale is the chosen
  private route.

## Don't

- Don't deploy to a stranger's host from a checkout through the owner's controller.
- Don't let either setup mode silently take over a host or data owned by the other.
- Don't weaken image pinning, secret-file handling, exposure policy, or manual restore requirements to simplify setup.
- Don't add setup logic to `scripts/`, `host/` or `modules/`, or make them depend on `setup/`.
