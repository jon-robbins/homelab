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

## Traefik

Traefik is the edge reverse proxy (`modules/edge/traefik.compose.yml`, the official image). It joins `homelab_net`
and reaches services by container name. Configuration is files only: there is no Docker socket, no dashboard and no API.

- **Static configuration** is the platform's (`config/traefik/traefik.yml`). A site cannot change it.
- **Routes** are the site's `edge/traefik.yml`, inlined into the release. Start from
  `config/traefik/routes.example.yml`. Hosts use `{{ env "BASE_DOMAIN" }}`.
- **Certificates** come from the `cloudflare` resolver (DNS-01, `CLOUDFLARE_TOKEN`). Name the domains on one router,
  as the example does; the wildcard certificate then serves every host below it.

### Traefik routes

Dynamic configuration uses Traefik's file provider on `/etc/traefik/dynamic/`. The platform supplies `health.yml`
(routing `/healthz` on the loopback `ping` entry point) and the site supplies `routes.yml` (from `edge/traefik.yml` in
the site repo, inlined into the release as a Compose config). The platform ships an example file at
`config/traefik/routes.example.yml`.

- Define `routers`, `middlewares`, and `services` in `edge/traefik.yml`.
- Access is controlled by `entryPoints`: routers without `entryPoints` join only `tailnet` (the default).
- Match rules use Go templating, such as `Host(`{{ env "BASE_DOMAIN" }}`) && PathPrefix(`/sonarr`)`.
- For applications expecting path prefixes stripped, attach a `stripPrefix` middleware. For apps with configurable URL
  bases (Sonarr, Radarr, Prowlarr, Bazarr), configure the URL base in the app settings.
- Gated routes forward authentication to Authentik using `forwardAuth` middleware, paired with `headers` middleware
  to prevent header spoofing.
- Where routes overlap, set `priority` explicitly: Traefik orders them by rule length, not declaration order.
- A route change reaches Traefik only through a recreate, so a release is the only way to change routes. After
  changing `edge/traefik.yml`, merge to the site repo and apply the edge yourself ([operations](operations.md#edge)) or
  let `homelab-edge-update` apply it.

### Entry points decide who can reach a route

| Entry point | Container port | Published by |
|---|---|---|
| `tailnet` (default, TLS) | 4443 | the module, as `${TAILSCALE_IP}:443` |
| `tunnel` (plain HTTP, for `cloudflared`) | 5443 | the site's override, usually on loopback |
| `lan` (plain HTTP) | 8080 | the site's override |
| `lan-tls` | 8443 | the site's override |

A router with no `entryPoints` joins only `tailnet`. Only a route meant to be public names `tunnel`. An entry point
that is not published cannot be reached, and one bound to every interface must be in `exposed`
([0015](decisions/0015-exposure-policy.md)). `ipAllowList` is a second layer on private routes; never use it to tell
tunnel traffic from LAN traffic, because tunnel requests arrive from the Docker bridge, a private address.

Where routes overlap, set `priority`: Traefik orders them by rule length, not file order. A route change reaches
Traefik only through a recreate, so a release is the only way to change routes.

### Health

The healthcheck calls `/healthz`, which exists only when every dynamic file loaded. A syntax or template error in
the routes file makes the container unhealthy, so the edge updater rolls back. A valid file that routes wrongly
(for example, a router naming a missing service) still loads: only that router is dropped, so check the routes
after an edge release.

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
`config.yml` lives in the site repo's `edge/` folder; the tunnel credentials JSON is a host file. Tunnel ingress
rules are managed remotely in the Cloudflare Zero Trust dashboard. Route only the hostnames you want public, and put
Cloudflare Access in front of anything that has no login of its own. If a UI breaks behind Cloudflare, disable
Rocket Loader for that hostname.

## Plex remote access

Plex remote access needs its port (32400) reachable from the internet, because video cannot go through the
Cloudflare tunnel. To do that safely:

1. Forward only TCP 32400 on the router to the host. Nothing else for Plex.
2. In Plex settings: *Secure connections* = **Required**; *Manually specify public port* = 32400; leave
   *List of IP addresses and networks that are allowed without auth* empty.
3. Keep Plex current: its image is in the app tier, so patch and minor updates deploy unattended
   ([0014](decisions/0014-dependabot-automerge.md)). Check Plex security advisories when a new one is announced.
4. Plex's media mounts are read-only in the module; keep them that way.
