# 0016: homelab-mcp is the agent control surface, over Tailscale only

- **Status:** Accepted (2026-10-24, owner decision)
- **Code:** `apps/homelab-mcp/`, the `homelab-mcp` entries in `.github/workflows/post-merge-build.yml` and
  `.github/workflows/ci.yml`
- **Related:** [0002](0002-public-platform-private-site.md), [0009](0009-image-trust.md),
  [0010](0010-secrets.md), [0015](0015-exposure-policy.md), [guide](../mcp.md)

## Context

Media changes (delete a release, move a file into the library, approve a request, remove a torrent) are runtime
state, not platform configuration. Routing them through a pull request and a signed release is the wrong tool, and
doing them by hand over SSH leaves no audit trail. The pre-migration MCP server did this job and was retired with
the old platform; it is restored here with a narrower, gated surface.

## Decision

1. `homelab-mcp` is an app-tier service built from `apps/homelab-mcp` as a first-party image, digest-pinned like
   every other image.
2. It is reachable only from the LAN or Tailscale: its port binds the Tailscale address, and every request needs a
   bearer token read from a secret file. It is never routed through the Cloudflare tunnel.
3. Tools that change anything register only when `HOMELAB_MCP_ENABLE_MUTATIONS=1`. Destructive tools need the
   exact `expected_action` string the server computes (tier 1); lower-risk ones need `confirm=true` (tier 2).
   Every gated attempt, applied or refused, is appended to the audit log.
4. File tools act only below `HOMELAB_MCP_FILE_ROOTS`. A root itself cannot be moved or deleted, symlinks are not
   followed out of a root, and delete moves the entry into that root's `.homelab-trash`. Only `media_trash_empty`
   removes data permanently.
5. Library removals go through the owning app's API (qBittorrent, Radarr, Sonarr, Seerr) so the app's records
   match the disk.
6. Site-specific tools come from extensions named in `HOMELAB_MCP_EXTENSIONS` (Python entry points). A plugin ships
   its own image layered on the public one; the public image never contains plugin code.

## Consequences

- Anyone holding the token on the tailnet can delete media. Deletes are recoverable until the trash is emptied.
- Remote agents must be on the tailnet. Hosted connectors that call in from the internet cannot reach it.
- Large moves across disks run synchronously; a client may time out while the move completes.

## Don't

- Don't expose the MCP through the tunnel or a port forward; that needs a new record superseding this one.
- Don't add a tool that deletes without passing through the trash.
- Don't widen the file roots to the host filesystem or follow symlinks out of a root.
- Don't put plugin code or private tool names into this repository; use an extension.
