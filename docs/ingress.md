# Ingress: how traffic reaches services

## Choosing a path

| Path | Use it for | Pros | Cons |
|---|---|---|---|
| **LAN** | everything, at home | nothing to configure | home only |
| **Tailscale** | admin UIs (*arr apps, qBittorrent, Prowlarr) and anything personal, from anywhere | no open ports; per-device identity | every client needs Tailscale |
| **Cloudflare Tunnel** (`cloudflared`) | web apps you want reachable without Tailscale, such as a request page for friends | no open ports; Cloudflare in front | **no video streaming** (Cloudflare's terms); Cloudflare sees the traffic |
| **Router port forward** | only media streaming that must work without Tailscale (Plex remote access), and the peer ports (torrent, Soulseek) | works with every client | the service is directly on the internet: it must be patched fast and hardened ([security](security.md)) |

The default is: admin UIs on LAN and Tailscale only, web apps through the tunnel if needed, and a port forward
only for the media server's streaming port.

## Caddy

Caddy is the edge reverse proxy (`modules/edge/caddy.compose.yml`, image built from `config/caddy/Dockerfile`
with the Cloudflare DNS module). It joins `homelab_net` and reaches services by container name.

Certificates: Caddy uses the ACME DNS-01 challenge with `CLOUDFLARE_TOKEN`, so it gets real certificates for
`*.BASE_DOMAIN` without port 80 or 443 being reachable from the internet. In the site's Caddyfile, a site block
uses a `tls` block with `dns <provider> {env.CLOUDFLARE_TOKEN}`, or the equivalent global `acme_dns` option.

Binds: Caddy listens on the host's Tailscale address for 443 (`${TAILSCALE_IP}:443`). The `80` and `8443`
listeners in the module are on all interfaces today; see [0015](decisions/0015-exposure-policy.md).

### Caddy routes

The platform ships an example Caddyfile at `config/caddy/Caddyfile`; your real routes live in the site repo's
`edge/Caddyfile`, inlined into the release as a Compose config.

- Use `handle /service*` when the app is configured with that URL base and expects to see the prefix (Sonarr,
  Radarr, Prowlarr and Bazarr with a URL base set in their settings).
- Use `handle_path /service*` when the app expects the prefix removed (Seerr, qBittorrent).
- Restrict a route to trusted sources with a matcher:

  ```caddyfile
  @trusted remote_ip private_ranges <tailscale-cidr>
  handle /sonarr* {
      handle @trusted {
          reverse_proxy sonarr:8989
      }
      respond 403
  }
  ```

  `private_ranges` covers loopback and the RFC 1918 LAN ranges; Tailscale addresses come from the CGNAT range
  (`100.64/10`), which you add yourself.
- End every site block with a catch-all `handle { respond 404 }` so unknown paths don't fall through.

After changing the Caddyfile, merge to the site repo and apply the edge yourself
([operations](operations.md#edge)).

## Service names on `homelab_net`

Containers reach each other by service name on the `homelab_net` bridge, never by `localhost`:

| Service | Internal URL |
|---|---|
| sonarr | `http://sonarr:8989` |
| radarr | `http://radarr:7878` |
| prowlarr | `http://prowlarr:9696` |
| bazarr | `http://bazarr:6767` |
| qbittorrent | `http://qbittorrent:8080` |
| overseerr (Seerr) | `http://overseerr:5055` |
| autobrr | `http://autobrr:7474` |
| flaresolverr | `http://flaresolverr:8191` |
| jellyfin | `http://jellyfin:8096` |
| plex | `http://plex:32400` |

## Cloudflare Tunnel

`cloudflared` runs in the edge tier with host networking (`modules/edge/cloudflared.compose.yml`). Its
`config.yml` lives in the site repo's `edge/` folder; the tunnel credentials JSON is a host file. Create the tunnel
once with `cloudflared tunnel login` and `cloudflared tunnel create <name>`, route only the hostnames you want
public, and put Cloudflare Access in front of anything that has no login of its own. If a UI breaks behind
Cloudflare, disable Rocket Loader for that hostname.

## Plex remote access

Plex remote access needs its port (32400) reachable from the internet, because video cannot go through the
Cloudflare tunnel. To do that safely:

1. Forward only TCP 32400 on the router to the host. Nothing else for Plex.
2. In Plex settings: *Secure connections* = **Required**; *Manually specify public port* = 32400; leave
   *List of IP addresses and networks that are allowed without auth* empty.
3. Keep Plex current: its image is in the app tier, so patch and minor updates deploy unattended
   ([0014](decisions/0014-dependabot-automerge.md)). Check Plex security advisories when a new one is announced.
4. Plex's media mounts are read-only in the module; keep them that way.
