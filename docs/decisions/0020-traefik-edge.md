# 0020: Traefik from the official image is the edge proxy

- **Status:** Accepted (2026-11-07; Proposed 2026-11-02). The owner accepted it on the live evidence in **Gate
  evidence**, with three open items taken as known gaps rather than proven: the runtime half of G4, G5 at the live
  provider, and G9. Production has served from Traefik and cloudflared since the cutover (certificates issued
  2026-11-04), and Caddy is removed from the repo and no longer runs on the host
- **Supersedes:** in [0009](0009-image-trust.md), "the only first-party image is caddy" and the Caddy build,
  attestation and digest injection for the edge; in [0008](0008-compose-policy.md), the `NET_BIND_SERVICE`
  exception for the Caddy executable; in [0016](0016-edge-updater.md), "Caddy's admin API answers" as the edge
  health signal, decision 2 (`up` now also removes services the release dropped) and decision 5 for Traefik minor
  bumps (patch only); in [0015](0015-exposure-policy.md) step 3, "the Caddy admin API" and "a `remote_ip` guard"
- **Code:** `host/homelab-edge-update`, `config/release/image-policy.json`, `config/release/cap-allowlist.json`,
  `.github/dependabot.yml`, `.github/workflows/dependabot-automerge.yml` (P4 adds the module and config/traefik/)
- **Related:** [0001](0001-compose-modules.md), [0003](0003-tiers.md), [0004](0004-signed-full-state-releases.md),
  [0005](0005-slim-controller.md), [0010](0010-secrets.md), [0011](0011-complexity-budgets.md),
  [0018](0018-local-setup-path.md), [0019](0019-shared-media-mount.md), [ingress](../ingress.md),
  [operations](../operations.md#edge), [security](../security.md)

## Context

The edge proxy needs DNS-01 certificates from Cloudflare, so tailnet-only names get real certificates. The official
Caddy image has no Cloudflare DNS module, so we built our own image, attested it, copied it to GHCR and injected
its digest at release time. That pin went stale. The executable carried a file capability that fails under
`cap_drop: [ALL]`, which forced a capability exception into policy. Strangers on the local setup path don't run our
build pipeline, so a first-party image only stays fresh for them through extra machinery (an ordered tag scheme
that Dependabot can follow). Without that, it doesn't stay fresh at all.

The official Traefik image ships the Cloudflare DNS provider (lego). It reads a secret from a file through any
`*_FILE` variable, and it has a built-in healthcheck command. Dependabot follows it like every other upstream pin.

## Decision

1. **Image.** The edge proxy is `docker.io/library/traefik:v3.x.y@sha256:…`, digest-pinned and Dependabot-followed
   like any upstream image ([0009](0009-image-trust.md)). We don't build, wrap or re-tag it, and it gets no entrypoint
   script. With seerr-oidc as the only first-party image left, the build, attest and inject pipeline stays for it alone
   and is retired with it.
2. **Configuration is files, never discovery.** Traefik uses only the file provider. There is no Docker provider, no
   socket and no socket proxy, and labels configure nothing.
   - *Static configuration* is owned by the platform: `config/traefik/traefik.yml`, mounted at
     `/etc/traefik/traefik.yml`, where both `traefik` and `traefik healthcheck` find it. Nothing else may set static
     options. It defines fixed entry points: `tailnet` (:4443, the only `asDefault`), `tunnel` (:5443), `lan` (:8080),
     `lan-tls` (:8443), and `ping` on loopback :8082 (never published). It also defines:
     - one ACME resolver `cloudflare` (DNS-01, storage `/acme/acme.json`, no email, explicit public resolvers for
       lego's zone lookup);
     - an RSA2048 key and the ISRG Root X1 chain (`preferredChain`), which Plex needs. There is no second resolver:
       Traefik skips issuing a name that a stored certificate already covers, wildcards included, and router
       certificates from every resolver share the default TLS store
       ([provider.go](https://github.com/traefik/traefik/blob/v3.7.17/pkg/provider/acme/provider.go#L965-L998),
       [isDomainAlreadyChecked](https://github.com/traefik/traefik/blob/v3.7.17/pkg/provider/acme/provider.go#L1088-L1097)),
       so a second resolver would not isolate certificates. RSA handshakes cost little here;
     - the file provider on `/etc/traefik/dynamic/`;
     - every entry point with `aliasHeadersStrategy: delete`, `forwardedHeaders.trustedIPs: []`, and the
       encoded-character options set explicitly, so a Traefik minor release can't flip them;
     - `api`, the dashboard and plugins off, and version checks off (usage reporting is opt-in in Traefik, so it stays at its default).
   - *Dynamic configuration* has two files in that directory. The platform's `health.yml` routes `/healthz` on `ping`
     to `ping@internal`. The site's `routes.yml` holds routers, services and middlewares, and comes from
     `edge/traefik.yml` in the site, inlined by the renderer as a Compose `configs` entry
     ([0008](0008-compose-policy.md)). The owner's routes live in the private site repo. A stranger's file is
     intended to be generated by setup into their local site directory. Route generation is deferred; the current
     setup engine consumes an existing site read-only. The platform ships a commented example at
     `config/traefik/routes.example.yml` with no private values. Hosts use `{{ env "BASE_DOMAIN" }}`; certificate
     domains are set on the routers.
   - Overlapping rules set `priority` explicitly. By default Traefik orders routes by rule length, not by the order
     they are declared.
   - A config change reaches Traefik only through a recreate, so a release is the only way to change routes.
3. **Exposure** ([0015](0015-exposure-policy.md), [0019](0019-shared-media-mount.md)). All listeners are on unprivileged
   container ports. The module publishes only `${TAILSCALE_IP}:443:4443`. A site publishes any other entry point in
   its override, and a port bound to every interface must be in `exposed`. An entry point that isn't published
   can't be reached. Which entry point a router joins decides who can reach it:
   - Only routers meant to be public join `tunnel`. A router with no `entryPoints` joins only `tailnet`.
   - `ipAllowList` is defense in depth on private routers. It never separates tunnel traffic from LAN traffic,
     because tunnel traffic arrives from the Docker bridge address.
   - No dashboard or admin API exists. Unmatched requests get Traefik's 404.
4. **Hardening.** The edge proxy runs as `user: "${PUID}:${PGID}"` with `cap_drop: [ALL]` and no `cap_add`,
   no `read_only` (a release delivers config files as inline content, which Compose can't inject into a read-only service; [0008](0008-compose-policy.md)), `no-new-privileges`, `homelab_net`, and one stateful bind
   `${APP_DATA_DIR}/traefik/acme → /acme` for `acme.json` (0600; Traefik refuses a looser file). The `NET_BIND_SERVICE` exception in
   [0008](0008-compose-policy.md) and `cap-allowlist.json` is removed when Caddy is.
5. **Secret** ([0010](0010-secrets.md)). The token stays the `CLOUDFLARE_TOKEN` file secret. The module sets only
   `CF_DNS_API_TOKEN_FILE=/run/secrets/CLOUDFLARE_TOKEN`, a path. lego reads the file itself. The value never
   appears in `environment:`, a config or a release.
6. **Edge updater** ([0016](0016-edge-updater.md)). Health is busybox `wget` (already in the image) against `/healthz` on the loopback `ping` entry point,
   `http://127.0.0.1:8082/healthz`. `traefik healthcheck --ping` is not used, because it stays green when the dynamic
   configuration fails to load, and `/healthz` exists only when it loaded. Traefik minor bumps are not merged
   unattended, because minors have flipped security defaults; patch bumps still are. The updater's `up` adds
   `--remove-orphans`, so applying an `edge.json` gives the edge project exactly that file's services, on apply and
   on rollback. A proxy swap and its rollback then work like any other edge change. Rollback keeps both proxies'
   certificates, because each keeps its own certificate store. The controller still never applies the edge tier,
   starts nothing the owner stopped and uses `--no-deps`. Ping only shows that Traefik is running, not that its
   routes loaded, so route correctness is checked outside the updater (owner: a verify runbook after the merge, with
   rollback by revert; setup: its edge phase verify and GATE-1).
7. **Login gate** ([0018](0018-local-setup-path.md)). Traefik only authenticates; Authentik authorizes.
   - Every router starts with a `headers` middleware that deletes `Remote-User` and the named `X-Authentik-*`
     headers.
   - Gated routers then use `forwardAuth` to the embedded outpost
     (`http://authentik-server:9000/outpost.goauthentik.io/auth/traefik`, `trustForwardHeader: true`) with
     `authResponseHeadersRegex: (?i)^x-authentik-`. That deletes any forged `X-Authentik-*` header before setting
     Authentik's values.
   - The entry points' alias strategy deletes underscore and dot forms of these headers.
   - With Authentik down, gated routes fail closed with 500.
   - The outpost path `/outpost.goauthentik.io/` routes to Authentik ahead of gated routes.
   - Authentik admits only pre-approved people. There is no enrollment, self-signup or invitation flow. Someone
     without an account gets the login page and nothing else, with no hint about which apps exist.
   - Authorization moves into Authentik host by host: each application covers one host, and its policy bindings
     decide who can use it. The family host (Jellyfin web, Seerr) is bound to `family` and `admins`. The admin host
     (every other UI) is bound to `admins` only. A policy denial reaches the client as Authentik's access-denied page
     with HTTP 200, not 403, and the backend gets no request. Both are covered by the wildcard certificate. Traefik
     never matches on identity headers.
   - Native Jellyfin clients can't complete a browser login, so they reach Jellyfin directly on its tailnet port
     using Jellyfin's own accounts ([0018](0018-local-setup-path.md) item 4). The gate covers only the web route.
   - GATE-1's probe becomes a transient, digest-pinned `traefik/whoami` container that setup starts for the proof
     and removes afterwards, behind the same middleware chain on both hosts. The static `/.homelab/whoami` responder
     is gone. The setup engine on `main` has no GATE-1 step or Authentik wiring, so this probe is deferred to the
     wizard's gate rebuild. The deferral does not block acceptance of this decision.
8. **Budgets** ([0011](0011-complexity-budgets.md)). There are no budget changes. `host/` gains one flag on an existing
   line. The controller and renderer don't change. The original estimate assumed setup already generated a Caddy
   snippet; no such generator exists in the current engine. A new route generator is deferred to separate setup work.
   The platform removes a Dockerfile, an entrypoint shim, the Caddy module and example, a build matrix entry, a
   Dependabot entry, an allowlisted GHCR destination and a capability exception, and adds one module and one example.
   The net is about −20 lines and one image build fewer.

## Gate evidence

Spike `bc1f937` on branch `spike/traefik-edge` reported `spike/traefik/FINDINGS.md` (66 assertions) covering G1, G3
(except a real issuance), G4, G5 (fail closed is 500), G7 (parity with Caddy; native-Linux source addresses
unproven) and G8 (`/healthz` variant), with G9 partial. The
[findings](https://github.com/jon-robbins/homelab/blob/bc1f937f3c727e08e5cdf347c2290232c5cd4efb/spike/traefik/FINDINGS.md)
and adjacent `EVIDENCE.txt` were recovered from git history on 2026-11-05. They describe the historical spike,
not acceptance of the current private site. Current Docker tests use the platform's pinned image
`docker.io/library/traefik:v3.7.17@sha256:30ff6249…`
in `modules/edge/traefik.compose.yml`.

| Gate | Evidence as of 2026-11-07 | Status |
|---|---|---|
| G1 Hardening | live edge container: `PUID:PGID`, `cap_drop: [ALL]`, no `cap_add`, `no-new-privileges` (docker inspect); `test_render_docker.py` asserts zero effective capabilities and NoNewPrivs. `getcap` on the binary was not run | confirmed live |
| G2 Ports | live: tailnet publishes the tailnet address only, tunnel is loopback only, `lan` and `lan-tls` bind every interface and are on the site's `exposed` list, `ping` is unpublished; real traffic served on all four. Routers not joining `tunnel` are refused through the public path (Cloudflare TLS refusal or no DNS record) and the app is never reached. `asDefault` is set on `tailnet` only, but every production router names `entryPoints`, so the default path is proven only synthetically | confirmed live; `asDefault` default synthetic only |
| G3 Token and ACME | live: every served hostname presents a Let's Encrypt production certificate, RSA-2048, chain ending at ISRG Root X1, issued 2026-11-04, no staging or self-signed certificates; `acme.json` is mode 0600 (owner-confirmed) | confirmed live |
| G4 Header stripping | deployed config: every entry point has `aliasHeadersStrategy: delete` and `trustedIPs: []`, every router runs the stripping middleware first, file provider only, no api or plugins. Unauthenticated requests with forged `Remote-User` and `X-Authentik-*` headers get the login redirect, identical to clean requests. Not shown at runtime: header absence at an upstream, and alias deletion | confirmed in config; runtime half is an accepted gap |
| G5 Probe | spike with an authentication stub (fail closed = 500). Live: the 2026-11-05 Authentik outage returned HTTP 500 on Authentik's own host route, from a log that no longer exists; gated application routes were never observed failing closed | unverified at the live provider; deferred with GATE-1 |
| G6 Authorization in Authentik | the owner ran the family, admin and unknown-user checks on the live outpost and reported them working. Per-cell results were not recorded. An unknown Google account sees an HTTP 500 on the source callback, not Authentik's denial page; access is refused, but a flow for unknown users is still to be added | owner-confirmed; per-cell evidence not recorded |
| G7 Source addresses | live, native Linux: tailnet and LAN-addressed sources keep their addresses; loopback, host-local and Cloudflare-tunnel traffic all arrive as the Docker bridge gateway, so `ipAllowList` cannot separate tunnel from LAN and decision 3's entry-point rule is required. No remote tailnet peer or other LAN device was tested | confirmed live |
| G8 Health | native-Linux Docker test: good routes pass the probe; a broken `routes.yml` fails the provider and the probe. Passed in the isolated VM gate on 2026-11-05. Production `/healthz` passes | accepted on this evidence by the owner |
| G9 Dependabot | the pin follows Dependabot like any upstream image; no Traefik bump has been observed yet. Ignore rules were added to `dependabot.yml` after a major Postgres bump and a legacy Sonarr tag misranking ([0014](0014-dependabot-automerge.md)); the probe pin remains deferred with GATE-1 | deferred; does not block acceptance |
| G10 Swap | isolated native Linux VM: swap, forced-unhealthy rollback, failed-hash skip and manual recovery with self-signed fixtures. Then the real production cutover, with real certificates issued (G3). No production rollback drill, deliberately: it would take the edge down | accepted by the owner (2026-11-06) on the rehearsal, the cutover and G3 |

Acceptance is an owner decision, not a claim that every gate is proven. G4 (runtime half), G5 (at the live provider)
and G9 stay open as known gaps, and G6 rests on the owner's report. The current Docker test file passed all five tests
on Docker Desktop and again in the isolated native Linux VM on 2026-11-05. The full VM gate passed, including controller
and wizard end-to-end sessions and the image build. The separate VM recovery rehearsal used the shipped updater and
configuration-digest function, pinned proxy images and synthetic site configuration. It did not use production
credentials, and it did not prove real DNS issuance or Authentik authorisation (both checked live afterwards, see the
table) or production certificate-store recovery. See the
[handoff](../superpowers/plans/2026-11-05-traefik-retirement-handoff.md) for evidence provenance and the earlier
production incident, which is not rehearsal evidence.

- **G1 Hardening:** the pinned image runs as `PUID:PGID` with `cap_drop: [ALL]`, no `cap_add` and
  `no-new-privileges`, and `getcap` on the binary is empty.
- **G2 Ports:** `${TAILSCALE_IP}:443:4443` and the other unprivileged container ports serve as expected, and
  `asDefault` keeps routers without `entryPoints` off `tunnel`.
- **G3 Token and ACME:** lego in the pinned version reads `CF_DNS_API_TOKEN_FILE`, tolerates a trailing newline, and
  needs no more than DNS edit and zone read on one zone. It issues from the staging CA with no email. `acme.json` is
  created 0600 by the non-root user on the acme bind. G3 covers RSA issuance: an RSA2048 key on the ISRG Root X1 chain
  is proven only by a real issuance, which the cutover checks.
- **G4 Header stripping:** forged `X-Authentik-*`, `Remote-User` and their underscore and dot aliases never reach the
  upstream through the gate chain.
- **G5 Probe:** `traefik/whoami` behind the chain shows only Authentik's identity. With Authentik stopped, every gated
  route fails closed; record the exact status, since forwardAuth may answer 500 rather than 502 to 504.
- **G6 Authorization in Authentik:** using per-host applications on the embedded outpost, a family user reaches the
  family host and is refused on the admin host, an admin reaches both, and an unknown user and a user with no group
  reach neither. No enrollment or recovery flow is reachable without signing in.
- **G7 Source addresses behind Docker NAT:** record what Traefik sees as the client address for tailnet, LAN,
  loopback (tunnel) and host-local traffic, and confirm the entry-point rule in decision 3 is required.
- **G8 Health:** with the platform's `health.yml` and a broken site `routes.yml` in the same provider directory,
  `/healthz` fails at startup, so the container is unhealthy. A passing `/healthz` proves only that the dynamic files
  parsed on a fresh start, not that the expected routes are present.
- **G9 Dependabot:** a dry run on a branch bumps `docker.io/library/traefik:vX@sha256` in `modules/edge` and the probe
  pin in `setup/config`. That pin is deferred with GATE-1 to the wizard's gate rebuild and does not block acceptance.
- **G10 Swap:** on a disposable Docker host, the updater with `--remove-orphans` swaps Caddy for Traefik, rolls back to
  Caddy on a forced failure, and loses no certificate.

## Consequences

- The edge has no first-party image. Strangers get upstream Traefik updates through the same Dependabot and release
  flow as every app image.
- Routes are YAML, about two to three times the length of the Caddyfile. The example documents them; setup route
  generation remains deferred.
- A dynamic file that fails to load makes the container unhealthy (no `/healthz`), so the updater rolls back. A file
  that loads but routes wrongly is caught by the owner's post-merge verify runbook and setup's verify. A few minutes
  of broken routes before a revert are accepted.
- Moving from paths to two hosts changes stranger URLs for admin UIs. No stranger host exists yet.
- The cutover issues one new certificate. Rolling back to Caddy reuses Caddy's stored certificates.

## Rejected

- **Keep Caddy with ordered first-party tags:** it keeps a custom build, an entrypoint shim, a capability exception
  and a tag scheme whose only job is to keep our own image fresh. Strangers would depend on our pipeline to get
  upstream Caddy fixes.
- **A community Caddy image with the Cloudflare module:** an unvetted publisher would become the trust anchor for the
  most exposed service on every host, with a new namespace and irregular tags.
- **Tailscale Serve:** it gives no LAN path and no tunnel origin, serves only `*.ts.net` names, ties ingress to one
  vendor and has no forward-auth step.
- **Docker-label discovery:** it needs the Docker socket, which is root on the host and banned by
  [0008](0008-compose-policy.md). A socket proxy still exposes container metadata and adds a service.
- **Traefik plugins for header-based authorization:** plugins are code fetched at runtime, outside digest pinning.

## Don't

- Don't enable the Docker provider, mount the socket or add a socket proxy.
- Don't enable the API or dashboard, plugins, or experimental features.
- Don't build, wrap or re-tag the Traefik image, or give it an entrypoint script.
- Don't attach a private router to `tunnel`, and don't rely on `ipAllowList` to tell tunnel traffic from LAN traffic.
- Don't let a site set static options; a new entry point is a platform change.
- Don't authorize on identity headers in Traefik; group rules live in Authentik.
- Don't put the token value in `environment:`; only the `_FILE` path goes there.
- Don't let the controller apply the edge tier.
