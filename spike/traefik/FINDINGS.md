# Traefik edge spike: findings

Question: can the official `traefik:v3` image replace our custom Caddy build as the edge reverse proxy, meeting every
requirement the Caddy edge meets today? Evidence-only; nothing outside `spike/traefik/` changed.

**Answer: yes, with five specific gaps (section "Gaps and surprises") and one policy change (image namespace).**
No custom image, no plugin, no capability, no Docker socket.

Reproduce everything: `spike/traefik/run.sh` (about 3 minutes; project `traefikspike`; tears itself down).
Last run: **66 assertions passed, 0 failed**; full transcript in [`EVIDENCE.txt`](EVIDENCE.txt).
Tested on Docker 29.8.0, arm64 (Docker Desktop), Traefik 3.7.17 (go1.27.3, lego v5.6.0).

## Verdicts

| # | Requirement | Status | One-line evidence |
|---|---|---|---|
| 1 | Hardening (pinned, non-root, `cap_drop: [ALL]`, no `cap_add`, `read_only`, no-new-privileges, no file caps, binds HTTPS, policy) | **PROVEN** | `CapEff=0000000000000000 NoNewPrivs=1 uid=502`; layer scan finds 0 file capabilities (control: official `caddy:2.11.7` has 1); listens on `:4443` and even `:443` with zero caps. `compose_policy.py` passes except the image namespace (`docker.io/library/traefik` is not allowlisted) |
| 2 | No Docker socket; file provider only; hot reload | **PROVEN** | 0 `docker.sock` mounts; only `file` provider starts; create/edit reloads in 1-3 s; an invalid edit keeps the old config |
| 3 | Secrets as files (Cloudflare DNS-01) | **PROVEN** (no real cert) | With `CF_DNS_API_TOKEN_FILE`: Cloudflare answers `403 9109: Invalid access token`. Without it: `some credentials information are missing`. Trailing newline is trimmed. `acme.json` is created 0600 on a bind volume under a read-only rootfs |
| 4 | Forward auth = `gate.caddy`; spoofed `X-Authentik-*` never reaches the backend | **PROVEN** | Auth OK, auth OK but header missing from the auth response, auth skipped, and auth failed all tested; recommended chain leaves no spoofed header. Caveats below |
| 5 | `/.homelab/whoami` probe | **PROVEN** (gate.py needs a 3-line change) | `traefik/whoami` + `replacePath: /api` returns the request headers as JSON; identity, forged-header, `Remote-User` and 403 behaviour match |
| 6 | LAN/tailnet guard (`ipAllowList`) | **PARTIAL** | Works; ignores forged `X-Forwarded-For`/`X-Real-Ip`. Behind Docker Desktop NAT the source is the bridge gateway. Native-Linux Tailscale source preservation cannot be proven on this Mac |
| 7 | Healthcheck `traefik healthcheck --ping` | **PROVEN, with a trap** | Works with no extra binary. But it stays green when the dynamic config is invalid; a `/healthz` route fixes that using the busybox `wget` already in the image |
| 8 | Path/host routing, with and without prefix strip | **PROVEN** | Keep vs strip, host match, redirect, zstd, built-in 404 all verified. 2 routes: 6 Caddy lines vs 17 Traefik YAML lines |
| 9 | Dependabot follows `traefik:vX.Y.Z@sha256:` | **PARTIAL** | Tag shape, index digest and 5-arch manifest verified; Dependabot cannot run locally. Repo already merges Dependabot bumps for the same shapes |
| 10 | What Caddy does that Traefik cannot | **PROVEN** | Listed below |

## Recommended exact choices

```yaml
image: docker.io/library/traefik:v3.7.17@sha256:30ff62491aacdb862ceb8c9edcf2627da638d7deafdd4b6dab32fe34f8aa3e90  # multi-arch INDEX digest
user: "${PUID}:${PGID}"
cap_drop: [ALL]            # no cap_add
security_opt: ["no-new-privileges:true"]
read_only: true
tmpfs: [/tmp]
ports: ["${TAILSCALE_IP}:443:4443"]      # same port strategy as the Caddy module: container listens high
environment: [BASE_DOMAIN=${BASE_DOMAIN}, CF_DNS_API_TOKEN_FILE=/run/secrets/CLOUDFLARE_TOKEN]
volumes: [bind ${APP_DATA_DIR}/traefik/acme -> /acme]                # acme.json; homelab.state: stateful
configs: static (/etc/traefik/traefik.yml, platform) + routes (/etc/traefik/dynamic/routes.yml, site)
healthcheck: ["CMD", "traefik", "healthcheck", "--ping"]             # or the /healthz variant, see item 7
```

Working module: [`module/traefik.compose.yml`](module/traefik.compose.yml). Static config: [`traefik/static.yml`](traefik/static.yml).

- **Image:** pin the `v3.7.17` tag's index digest, never a per-platform digest. The rolling tags `v3`/`v3.7` resolve to a
  different index digest than `v3.7.17` today (`e9338a01...` vs `30ff6249...`); we pin the exact tag.
- **Port strategy:** unchanged from Caddy. Container listens on `4443`; Docker publishes `${TAILSCALE_IP}:443`. Caddy needed
  `cap_add: [NET_BIND_SERVICE]` only because of the file capability on its binary; Traefik has none, and Docker already
  sets `net.ipv4.ip_unprivileged_port_start=0` in container namespaces, so even `:443` inside works with no caps.
- **Header strip:** `headers` middleware with empty values (enumerated names plus `Remote-User`) first, then `forwardAuth`
  with `authResponseHeadersRegex: (?i)^x-authentik-`. See item 4.
- **whoami method:** `traefik/whoami:v1.10.3` (namespace `traefik/` is already allowlisted) behind `replacePath: /api`.
- **Static-config hardening to set explicitly:** `aliasHeadersStrategy: delete`, all `encodedCharacters.allowEncoded*: false`,
  `forwardedHeaders.trustedIPs: []`, `sendAnonymousUsage: false`, no `api`/dashboard, ping on a loopback-only entrypoint.

---

## 1. Hardening: PROVEN

Commands and output: `EVIDENCE.txt`, "ITEM 1".

- `docker inspect`: `user=502:20 readonlyRootfs=true capDrop=[ALL] capAdd=[] securityOpt=[no-new-privileges:true] privileged=false`.
- Inside the container: `CapPrm/CapEff/CapBnd = 0000000000000000`, `NoNewPrivs: 1`, `touch /etc/x` -> `Read-only file system`.
- File capabilities: `docker save $IMG | python3 -I check-filecaps.py` reads the PAX `SCHILY.xattr.security.capability`
  header of every layer entry. Traefik: 0 hits. Positive control, the official Caddy image: `usr/bin/caddy` carries one
  (this is the stale-pin `cap_drop: [ALL]` exec failure). Traefik also runs end to end under `cap_drop: [ALL]` as non-root, which is the functional proof.
- Binds: `netstat` shows `:4443`, `:8000`; TLS handshake serves `CN=TRAEFIK DEFAULT CERT` over h2. The `traefik-443`
  service (`:443`, no caps) answers 404 over TLS.
- Policy: `policy-check.py` runs the repo's real `scripts/release/compose_policy.py` on the module (image, user, health,
  ports, caps, secrets, binds, `homelab.state`). Result: one violation,
  `image namespace is not permitted: docker.io/library/traefik:v3.7.17`. Adding `docker.io/library/traefik` to
  `config/release/image-policy.json` (in memory only here) clears it. `docker.io/library/postgres` is the precedent, and
  decision 0009 says that allowlist change must be its own change. The `whoami` service also passes (`homelab.probe` label,
  because a scratch image has no shell for a CMD healthcheck).
- Note: Traefik's image is Alpine with busybox; its entrypoint is a `sh` script. Both work under `read_only` and non-root.

## 2. No Docker socket, file provider only, hot reload: PROVEN

- `docker inspect` mounts and `docker compose config`: 0 occurrences of `docker.sock`. Startup log lists only
  `file.Provider`, plus Traefik's internal provider and the TLS-ALPN challenge helper (no network listener beyond the entrypoints).
- Hot reload (`providers.file.directory` + `watch: true`):
  new file -> 200 within ~4 s; in-place edit -> new backend served; **invalid YAML -> previous config keeps serving, logged
  `ERR Error occurred during watcher callback`**; container stays healthy.
- Docker Desktop caveat: deleting a file on a virtiofs bind mount did not deliver an event (a `touch` of another file did
  trigger the reload). On a named volume the delete was seen immediately. Linux bind mounts use inotify directly, so this
  is expected to be a Mac artefact; not proven on Linux.
- Single file bind (what Compose `configs:` creates): in-place write (`cat >`) reloads; replace-by-rename (`sed -i`) does not,
  because the mount pins the old inode. In production the edge updater recreates the container when the release's config
  changes (decision 0016), so reload-in-place is a convenience, not a dependency.
- Go templating in the dynamic file works: `Host(`{{ env "BASE_DOMAIN" }}`)` replaces Caddy's `{$BASE_DOMAIN}`.
  The **static** file has no env substitution (use flags or `TRAEFIK_*` env).

## 3. Secrets as files: PROVEN (cannot issue a real cert)

Setup: a local [Pebble](https://github.com/letsencrypt/pebble) ACME CA, three Traefik instances (same hardening), wildcard
domain under the reserved `example.com`, a random 40-character bogus token. `dnsChallenge.resolvers: ["1.1.1.1:53"]` was
needed so lego's SOA zone lookup works from inside Docker (the embedded resolver returned `SERVFAIL`).

| Instance | Observed |
|---|---|
| `CF_DNS_API_TOKEN_FILE=/run/secrets/CLOUDFLARE_TOKEN` | `cloudflare: failed to find zone example.com.: [status code 403] 9109: Invalid access token`. lego read the file and Cloudflare itself rejected it |
| same, file ends in `\n` | identical Cloudflare rejection (lego trims; no local "invalid header value" error) |
| no credential | `cannot get ACME client cloudflare: some credentials information are missing: ... CLOUDFLARE_DNS_API_TOKEN ...` |

- `acme.json`: created `-rw-------` by the non-root user on a bind-mounted `/acme` while `ReadonlyRootfs=true`.
  A pre-existing 0644 file is refused: `permissions 644 for /acme/acme.json are too open, please use 600`.
- Not proven: an actual issuance with a valid scoped token. `LEGO_CA_CERTIFICATES` (or `acme.caCertificates`) trusted the Pebble CA.
- Decision 0010 stays intact: no secret in `environment:` except the *path* to the file.

## 4. Forward auth equals gate.caddy: PROVEN, with caveats

Mapping (all in `traefik/dynamic/routes.yml`, tested against `stub/authstub.py`):

| gate.caddy | Traefik |
|---|---|
| `request_header -Remote-User` / `-X-Authentik-*` | `headers.customRequestHeaders: {Remote-User: "", X-Authentik-Username: "", ...}` (empty value deletes; enumerated names, 13 incl. `Remote-User`) |
| `forward_auth ... copy_headers ...` | `forwardAuth.address` + `authResponseHeadersRegex: (?i)^x-authentik-` |
| `reverse_proxy /outpost.goauthentik.io/*` (never gated) | router `priority: 1000`, no auth middleware |
| `respond "Not Found" 404` inside the gated route | catch-all router (priority 1) with the same auth chain, `noop@internal`, `errors` rewrites 418 to 404 |
| `admins_only` -> 403 | second `forwardAuth` into a loopback-only entrypoint (below) |

Proven (each is an assertion in `EVIDENCE.txt`, "ITEM 4"):

- Anonymous (even with forged identity headers) -> 302 to the login URL with `Set-Cookie` preserved; also for a random path.
- Auth succeeds: backend sees exactly the auth server's five identity headers; forged `X-Authentik-Username/Groups/Email`,
  `Remote-User` and the underscore alias `X_Authentik_Username` are gone.
- Auth succeeds **but the auth response lacks `Email`** (user "carol"): the forged `Email` still does not survive
  (Traefik deletes the named/matching request header before copying).
- Auth skipped (routes with only the strip middleware, and the outpost route): `Remote-User` and the 12 enumerated names are stripped.
- Authenticated, non-admin -> `/.homelab/admin` 403 even when sending a forged `X-Authentik-Groups: admins`; admin -> 200.
- Auth server stopped -> every route fails closed.

Caveats:

1. **No wildcard strip on routes without forwardAuth.** An unlisted `X-Authentik-Future-Thing` passes the `headers`
   middleware (variant A leaves it; `ungated` host in the transcript). The regex form (`authResponseHeadersRegex`) strips
   any `X-Authentik-*` but only on routes where forwardAuth runs, and does not cover `Remote-User` (variant B leaves it).
   Hence the chain: enumerated strip first, regex forwardAuth second. Unlisted names can still reach an ungated backend.
   In the gate design the only ungated route goes to Authentik itself.
2. **Fail-closed status is 500, not 502/503/504.** `gate.py` `FAIL_CLOSED = (502, 503, 504)` must add 500.
3. **`admins_only` has no native equivalent.** Router rules run before middleware, so `HeaderRegexp(X-Authentik-Groups, ...)`
   in a router rule cannot see the header forwardAuth sets (bob, a real admin, gets 404), but *does* match a client-forged
   header: never route on identity headers. Working replacement: `forwardAuth` -> `http://127.0.0.1:8083`, a loopback-only
   entrypoint whose router matches the header (after the first forwardAuth set it) and routes to whoami (200); its
   catch-all is `noop@internal` (418) rewritten to 403 by `errors`. It uses only Traefik and whoami, but is an unusual
   pattern. The idiomatic alternative is an Authentik application/group policy per hostname (admin hosts on their own
   hostname); that is host-based, not path-based, so it changes the site layout. Needs an architect decision.
4. `aliasHeadersStrategy: delete` is what drops `X_Authentik_Username`-style aliases (WSGI/CGI backends read them as the real header).
   Caddy has no such protection. Traefik logs a startup warning for any entrypoint without it.
5. Stub-only: the real Authentik outpost endpoint (`/outpost.goauthentik.io/auth/traefik`), `Set-Cookie` handling on a 200
   (`addAuthCookiesToResponse`), and relative `Location` redirects (`preserveLocationHeader`) must be re-verified in GATE-1 against real Authentik.
6. Set `trustForwardHeader: true` together with entrypoint `forwardedHeaders.trustedIPs: []`
   (documented as the supported setup; otherwise a startup warning and legacy partial stripping) and `maxResponseBodySize`.

## 5. GATE-1 probe: PROVEN

Traefik has no static-response handler. Smallest option: `traefik/whoami` (6 MB scratch image, runs uid 502, `cap_drop ALL`,
`read_only`) behind `replacePath: path: /api`, which returns the received headers as JSON. Identical results to what
`gate.py` checks: family member `alice|family|` (no `Remote-User`), admin `bob|family|admins|`, forged headers ignored.

`gate.py` must change from `user, groups, remote = reply.body.decode().split("|")` to reading
`reply.json()["headers"]` (`X-Authentik-Username`, `X-Authentik-Groups`, `Remote-User`, each a list). Byte-for-byte
`a|b|c` output is impossible without a custom image. No healthcheck binary exists in the image: use the policy-approved
label `homelab.probe: http://:8080/health`.

## 6. LAN/tailnet guard: PARTIAL

`ipAllowList.sourceRange` (Caddy `remote_ip`; `private_ranges` has no alias, list the CIDRs: the RFC 1918 ranges and Tailscale's CGNAT range).

- A client container inside the range: 200, and Traefik sees its real address (`X-Forwarded-For: 198.51.100.50`).
- Outside the range: 403; adding forged `X-Forwarded-For` (allowed address or 127.0.0.1) or `X-Real-Ip`: still 403.
  `forwardedHeaders.trustedIPs: []` makes the entrypoint overwrite client-supplied values (a forged `203.0.113.9` arrived at the backend as `198.51.100.1`).
- Behind Docker Desktop NAT the host reaches the published port as the bridge gateway (`198.51.100.1`), which is not in the list, so 403.
- Not provable here: on native Linux, a LAN or Tailscale client coming in through the published port keeps its real source
  address via iptables DNAT (so Tailscale's CGNAT range would work), whereas a connection from the host itself appears as the gateway.
  Caddy's `remote_ip` has exactly the same property, so this is parity, not a regression. If traffic arrives via
  `cloudflared`/another proxy, `forwardedHeaders.trustedIPs` plus `ipStrategy.depth` is the (untested) knob.

## 7. Healthcheck: PROVEN, with a trap

`healthcheck: {test: ["CMD","traefik","healthcheck","--ping"]}` works and reads the same static file
(`ping: {entryPoint: ping}`, `ping` entrypoint on `127.0.0.1:8082`, not published: status `healthy`, exit 0).
Unlike Caddy's admin API on `:2019` (reachable on `homelab_net`), nothing is exposed; `api`/dashboard are off.

Trap: a Traefik started with an **invalid dynamic file** (`Error while building configuration`) still answers `/ping`
with `OK` and reports healthy, serving 404 for everything, where Caddy would fail to start and the edge updater's
`--wait` (decision 0016) would roll back. Fix proven: a router on the `ping` entrypoint,
`healthz: {rule: Path(`/healthz`), entryPoints: [ping], service: ping@internal}`, and a compose healthcheck
`wget -q -O /dev/null http://127.0.0.1:8082/healthz` (busybox is in the image, so still no extra binary). It returns 0 on the
good config and 1 on the broken one. Recommend this variant so rollback keeps working.

## 8. Routing and line count: PROVEN

Behaviour (assertions, "ITEM 8"): `/sonarr` keeps its prefix (`GET /sonarr/api/v3?x=1`), `/overseerr` is stripped
(`GET /foo`), wrong `Host` -> 404, `/bazarr` -> `301 .../bazarr/`, `Accept-Encoding: zstd` -> `content-encoding: zstd`,
unmatched path -> built-in 404 (no catch-all needed), and a plain-HTTP entrypoint works for the `:80` block.

The two representative routes ([`two-routes.example.yml`](two-routes.example.yml), verified standalone by `run.sh`):

```yaml
http:
  routers:
    sonarr:
      rule: Host(`{{ env "BASE_DOMAIN" }}`) && PathPrefix(`/sonarr`)
      service: sonarr
    overseerr:
      rule: Host(`{{ env "BASE_DOMAIN" }}`) && PathPrefix(`/overseerr`)
      middlewares: [strip-overseerr]
      service: overseerr
  middlewares:
    strip-overseerr:
      stripPrefix: {prefixes: [/overseerr]}
  services:
    sonarr:
      loadBalancer: {servers: [{url: "http://sonarr:8989"}]}
    overseerr:
      loadBalancer: {servers: [{url: "http://overseerr:5055"}]}
```

Caddy: 6 lines (3 each). Traefik: 17 lines, roughly 3x, mostly the router/service split. Gotcha found: **default router priority is
rule length**, so `PathPrefix(/bazarr)` beat `Path(/bazarr)` and the redirect never fired until `priority: 100` was set.
Set `priority` explicitly wherever rules overlap.

## 9. Update path: PARTIAL

- `docker buildx imagetools inspect traefik:v3.7.17`: OCI index with `linux/amd64`, `arm64/v8`, `arm/v6`, `ppc64le`, `s390x`
  (plus attestation manifests). The pin in the module is that index digest, so one pin serves amd64 and arm64.
- Docker Hub tags: plain `v3.7.17`, `v3.7.16`, ...; the Windows variants (`v3.7.17-windowsservercore-...`) carry a suffix, and
  Dependabot only compares tags with the same suffix shape as the current one.
- Precedent in this repo's log: Dependabot docker-compose bumps already merged for `cloudflare/cloudflared` (#81, #57),
  `library/postgres`, and the `v`-prefixed `autobrr/autobrr` (#79, #55), all with the `tag@sha256:` form. `dependabot.yml`
  already covers `/modules/edge`. `dependabot-automerge.yml` merges patch **and minor** there.
- Not proven: Dependabot itself cannot run locally. Risk worth a decision: Traefik minors change security defaults
  (3.6.4 started rejecting encoded characters, 3.6.7 reverted that default; 3.7 added `aliasHeadersStrategy` and
  warnings). That is why `static.yml` sets those values explicitly. Consider patch-only automerge for Traefik.
- The `docker` ecosystem entry for `/config/caddy` and the first-party image build/attestation path
  (decision 0009) become removable.

## 10. Things Caddy does that Traefik cannot

1. **No static responses.** `respond "Not Found" 404`, `respond "Forbidden" 403`, `respond "..." 200` do not exist. Substitutes:
   unmatched requests get Traefik's built-in 404; `noop@internal` answers 418 and `errors` + `statusRewrites` maps it to 404/403 with an
   empty body (whoami `/health` as the error page); `ipAllowList` denials are a plain 403.
2. **No placeholders in responses** (`{http.request.header.X}`), hence the whoami JSON change in item 5.
3. **No conditional blocks on request/middleware-derived values.** `handle @not_admin` has no equivalent (item 4, caveat 3).
4. **No wildcard request-header removal** outside forwardAuth's regex (item 4, caveat 1).
5. **No env substitution in static config**; only the dynamic file supports Go templates (`{{ env "X" }}`).
6. **No named ranges** such as `private_ranges`; list CIDRs.
7. **No `caddy validate`-style dry run.** A bad dynamic file is logged, not fatal (item 7 trap).
8. **Router precedence is by priority (default rule length), not declaration order** (item 8 gotcha).
9. **No snippets/imports across files.** Reuse is via named middlewares and chains (and YAML anchors inside one file).
10. **Fail-closed status differs** (500), and explicit certificate domains are required per resolver (no automatic HTTPS from site addresses).
11. Not tested, flagged only: ACME `email` is static config (lego allows it empty; otherwise set via `TRAEFIK_*` env/flag);
    real-Authentik behaviours listed in item 4 caveat 5.

## Gaps and surprises (read these first)

1. `docker.io/library/traefik` is not in `config/release/image-policy.json`: the module fails policy until a separate change adds it.
2. `traefik healthcheck --ping` is green with a broken dynamic config; use the `/healthz` route variant or rollback (0016) loses its signal.
3. Without explicit `priority`, overlapping routes silently pick the longer rule.
4. `admins_only` needs either the loopback helper-entrypoint pattern (works, proven) or per-host Authentik policies (design change).
5. `gate.py` needs two edits: parse whoami JSON; accept 500 as fail-closed.
6. Traefik 3.7 prints startup warnings for `aliasHeadersStrategy`, `encodedCharacters`, `trustForwardHeader`, `maxResponseBodySize`;
   all four are set in the shipped config, which starts with zero warnings.

## Gap closure

| Gap | State | What closes it |
|---|---|---|
| 1 Image namespace | Patch ready | [`follow-ups/0001-allow-traefik-image-namespace.patch`](follow-ups/0001-allow-traefik-image-namespace.patch); must land as its own change (0009) |
| 2 Healthcheck trap | Closed in spike | `/healthz` route in `traefik/dynamic/routes.yml`, proven in `EVIDENCE.txt`; adopt it in the module |
| 3 Route priority | Closed in spike | Explicit `priority` on every overlapping route in `routes.yml` |
| 4 `admins_only` | Closed in spike, needs decision | Loopback helper entrypoint is proven; the per-hostname alternative needs an architect decision |
| 5 `gate.py` | Patch ready | [`follow-ups/0002-gate-py-traefik-whoami.patch`](follow-ups/0002-gate-py-traefik-whoami.patch), against `feat/ws-01-authentik`; compiles, not run against a live stack |

Both patches are not applied: this spike changes nothing outside `spike/traefik/`.

## Files

| File | Purpose |
|---|---|
| `run.sh` | reproduces everything; `KEEP=1` leaves the stack up, `./run.sh clean` removes it |
| `compose.yml` | spike stack (project `traefikspike`, network `198.51.100.0/24`, a documentation range) |
| `module/traefik.compose.yml` | proposed `modules/edge` file; `policy-check.py` runs the real policy on it |
| `traefik/static.yml`, `static-acme.yml`, `static-443.yml` | static configs (main, Pebble/Cloudflare, privileged-port variant) |
| `traefik/dynamic/routes.yml` | Caddyfile + gate.caddy equivalent, plus variants used as test fixtures |
| `stub/authstub.py` | Authentik-like forward-auth stub |
| `check-filecaps.py` | image file-capability scanner |
| `two-routes.example.yml` | the two representative routes |
| `EVIDENCE.txt` | transcript of the last full run |

## Sources

- Traefik ForwardAuth (`authResponseHeaders`, `authResponseHeadersRegex`, `trustForwardHeader`, `maxResponseBodySize`):
  https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/forwardauth/
- Headers middleware (empty value removes a header):
  https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/headers/
- Errors middleware (`statusRewrites`):
  https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/errorpages/
- EntryPoints (`forwardedHeaders`, `aliasHeadersStrategy`, `encodedCharacters`):
  https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/ and
  https://doc.traefik.io/traefik/security/header-aliases/
- IPAllowList (`sourceRange`, `ipStrategy`):
  https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/ipallowlist/
- ACME DNS challenge (provider env vars come from lego):
  https://doc.traefik.io/traefik/reference/install-configuration/tls/certificate-resolvers/acme/ and https://go-acme.github.io/lego/dns/cloudflare/
- Pebble test CA: https://github.com/letsencrypt/pebble (its CA certificate is copied out of the image at run time, not committed)
- Local: `docs/decisions/0009`, `0010`, `0015`, `0016`; `setup/config/authentik/gate.caddy` and `setup/wiring/gate.py` on `feat/ws-01-authentik`.
