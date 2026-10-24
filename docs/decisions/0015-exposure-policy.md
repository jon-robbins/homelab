# 0015: Nothing is reachable from the internet unless allowlisted

- **Status:** Proposed (2026-10-20, post-migration security audit)
- **Code (when implemented):** `modules/`, `scripts/release/compose_policy.py`, `host/install.sh`
- **Related:** [0008](0008-compose-policy.md), [0010](0010-secrets.md), [security](../security.md), [ingress](../ingress.md)

## Context

Docker publishes ports by writing its own iptables rules, which bypass UFW. A module that publishes `"8096:8096"`
listens on every interface, so a router port forward (or UPnP) exposes it directly. An internet-facing media
server on a known-vulnerable version was exploited this way: its API was abused to take the owner's token. The
platform had no rule about which ports may face the internet.

## Decision

1. Default bind for a published port is the LAN or Tailscale address (a `site.env` key), never `0.0.0.0`.
2. A port may bind all interfaces only if it is in an exposure allowlist with a reason. The intended entries are
   the media server's remote-access port (direct streaming; a Cloudflare tunnel may not carry video) and the
   torrent peer port.
3. Admin UIs (the *arr apps, qBittorrent, Prowlarr, the Caddy admin API) are reachable only from the LAN or
   Tailscale, through Caddy with a `remote_ip` guard.
4. The host installs `DOCKER-USER` rules: from the WAN interface, accept only allowlisted ports; drop the rest.
5. The router forwards only the allowlisted ports, and UPnP is off.
6. A check compares the host's listening sockets with the allowlist after each apply and fails the deadman ping
   on a difference.

## Consequences

- Remote access to admin UIs is through Tailscale only.
- Internet-facing services depend on fast patching ([0014](0014-dependabot-automerge.md)).

## Don't

- Don't open a router port forward to reach an admin UI remotely; use Tailscale.
- Don't publish a port without an interface address in a module.
