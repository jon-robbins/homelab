# Caddy-to-Traefik Cutover Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to complete these tasks in order. Keep the owner's site inventory, host names and route values in the private site repository.

**Goal:** Move the owner's edge from Caddy to Traefik through signed releases, migrate the setup path, then remove Caddy from the public platform.

**Architecture:** The site selects one edge proxy. Traefik uses the platform's static configuration and health route, the site's `edge/traefik.yml`, and a site override for the LAN and tunnel listeners. The edge updater applies the new signed `edge.json` with `--remove-orphans` and rolls back to Caddy when Traefik is unhealthy. A healthy Traefik that routes wrongly is fixed by a signed site revert.

**Spec:** `docs/decisions/0020-traefik-edge.md`, amended by Task 1. Follow `docs/ingress.md` and `docs/operations.md`.

## Owner decisions (2026-11-03)

- **Downtime is acceptable.** No quiet window, no manual rollback from an old release and no long observation. Security failures are silent rather than outages, so the authentication and exposure checks stay.
- **Authorisation by host.** Authentik decides access per application, and each application is one host. The base host becomes admin-only. Navidrome, the only family app on it, moves to its own one-level host so the tunnel's free Cloudflare certificate covers it. Its ungated Subsonic API (`/rest/`) moves with it; phone apps are reconfigured to the music host (2026-11-04), so the base host keeps no Navidrome path.
- **Plex keeps an RSA certificate with the ISRG Root X1 chain.** Plex's custom server URLs list all three proxied Plex hosts.
- **The tunnel origin stays HTTPS**, so the scheme reaching Authentik and the backends stays `https`. The site's cloudflared origins don't change. The client address the backends see stays the connector's, as with Caddy today.
- **Short rollback window:** 7 days after a successful cutover, then remove Caddy. Keep the last signed Caddy bundle, its pinned images and Caddy's certificate data for 90 days.
- **Out of scope:** moving Jellyfin's public host off the Cloudflare proxy is a separate change.

### Confirmed after verification

- **One RSA resolver for every host.** The single `cloudflare` resolver gets `keyType: RSA2048` and `preferredChain: "ISRG Root X1"`.
  - Why: a second resolver doesn't isolate certificates. Traefik skips issuing a name that any stored certificate, including a wildcard, already covers ([provider.go](https://github.com/traefik/traefik/blob/v3.7.17/pkg/provider/acme/provider.go#L890-L928)).
  - Cost: RSA handshakes for every host, which is negligible here.
- **Drop the friendly 503 page and the custom 403 text.**
  - Why: Traefik has no static responder. Its `errors` middleware needs an HTTP service to serve the page, which would be a new container and a new mechanism.
  - Instead: a backend that is down shows Traefik's own 502, and unmatched requests get Traefik's 404.

## Global constraints

- Only owner-approved merges produce signed releases for the owner's host. Never deploy from a checkout ([0004](../../decisions/0004-signed-full-state-releases.md)).
- No real domains, addresses, site routes, credentials or plugin names in this repository ([0002](../../decisions/0002-public-platform-private-site.md)).
- The Cloudflare token stays a file. Only `_FILE` paths appear in configuration ([0010](../../decisions/0010-secrets.md)).
- The controller never applies edge ([0016](../../decisions/0016-edge-updater.md)). Keep explicit interface binds and the site's `exposed` list consistent ([0015](../../decisions/0015-exposure-policy.md)).
- Thin wrappers around existing commands only. No route-description language, parity engine or new deployment path ([0011](../../decisions/0011-complexity-budgets.md)).
- Leave the site checkout's unrelated edits alone; prepare site PRs in an isolated checkout.

## Parallel execution

Three workstreams run at once. The owner's cutover (Task 4) is the only serial step that touches the host.

```text
WS-A platform   Task 1 ──PR──merge──────────────────────────────┐
WS-B site       Task 2 ──PR──merge──► owner verifies (Caddy) ─┼─► Task 3 rehearsal ─► Task 4 ─► 7 days ─► Task 6
                         └─ Task 3 drafted on a branch stacked on Task 2 ─┘
WS-C setup      Task 5 rename ─► PR ─► review ─► merge ──────────────────────────────────┘ (before Task 6)
```

### Workstreams

| WS | Tasks | Repository and worktree | Owner (risk) | Touches |
|---|---|---|---|---|
| A | 1 | platform, `feat/traefik-rsa-resolver` | Staff Engineer I (low: config, docs, tests) | `config/traefik/`, `docs/decisions/0020*`, `docs/operations.md`, updater comment, two test files |
| B | 2, then 3 | site, `feat/music-host`, then `feat/traefik-edge` stacked on it | Principal Engineer (high: auth, exposure, route parity) | Authentik blueprint, Navidrome override, `edge/*`, `overrides/traefik.yml`, `site.yaml`, site tests, `traefik-cutover.sh` |
| C | 5 | platform, `feat/setup-traefik` | Staff Engineer II (low: rename) | `setup/` only |
| — | review | read-only | System Architect | Reviews every PR before merge |

Each workstream owns its files. A and C share a repository but not files; each works in its own worktree. Task 6 starts only after A and C have merged, so it has no conflicts.

### Gates and merge order

1. **A merges first or alongside B.** Its platform release changes only Traefik's static configuration, which the owner's Caddy site doesn't select. The site needs no change first.
2. **B's Task 2 PR merges once the Architect has reviewed it.** The owner then runs the Task 2 checks under Caddy, with family and admin accounts and a phone app.
3. **Task 3 is drafted in parallel** on a branch stacked on Task 2. It is rebased after Task 2 merges and rendered against the merged A commit. Its rehearsal runs only after both have merged.
4. **Task 4 needs:** A merged and installed on the host, Task 2 verified, the Task 3 rehearsal passed, and the revert PR ready.
5. **C merges once its own tests pass and the Architect has reviewed it.** It doesn't block Task 4, but it must merge before Task 6.
6. **Every PR passes before merge:** `scripts/dev/gate` (platform) or site CI, the necessity review, and `verify-change`, `verify-quality` and `verify-security` for changes to auth or exposure.

### Agent limits

At most five agents at once: the three owners, the Architect and a CI babysitter if needed. An owner reports when its PR is open; the lead reviews it before the next stage starts.

### Owner actions, in one batch

1. Done: the defaults above are confirmed, and the rehearsal host is a disposable native-Linux VM with the production Docker and Compose versions and a clean snapshot to reset to. It has no tailnet identity and no Cloudflare token, so real tailnet source addresses and certificate issuance are checked live in Task 4.
2. Merge A. No host step: the host already runs tooling with `--remove-orphans` (checked 2026-11-04).
3. After Task 2 merges, run its checks with a family account, an admin account and a phone app. If family reach music remotely, run the provided script that creates the music host's DNS record.
4. Run Task 4 at a time of your choosing.

---

### Task 1: Platform prerequisites

**Files:** `config/traefik/traefik.yml`, `docs/decisions/0020-traefik-edge.md`, `host/homelab-edge-update` (comment only), `docs/operations.md`, `tests/modules/test_traefik.py`, `tests/release/test_render_docker.py`.

- [ ] Add `keyType: RSA2048` and `preferredChain: "ISRG Root X1"` to the `cloudflare` resolver. lego falls back to the default chain when no alternate chain matches.
- [ ] Update the tests that assert the resolver and the `acme.json` path and mode.
- [ ] Amend decision 0020:
  - §2: RSA key and X1 chain, and why there is no second resolver.
  - §7: host-based authorisation in Authentik. A policy denial is Authentik's access-denied page (HTTP 200), not a 403.
  - G3: covers RSA issuance.
  - G8: states that `/healthz` proves only that the dynamic files parsed on a fresh start, not that the expected routes are present.
- [ ] Correct the updater's header comment and `docs/operations.md:57`. The "edge change pending" notice does not fail the controller's deadman ping, because `scripts/controller/reconcile.py` uses only plan failures. Document that an edge rollback shows as a failed `homelab-edge-update.service`. Open an issue if that needs alerting.
- [ ] Run `scripts/dev/gate` and merge.
- [x] Host tooling: already at `87abf1d`, whose edge updater has `--remove-orphans` (checked 2026-11-04). The resolver change ships in the signed release, not the tooling, so no install is needed.

**Exit:** the platform renders a Traefik site with RSA certificates, and the host runs tooling whose updater removes orphans.

### Task 2: Move Navidrome to its own host, still under Caddy

**Files:** private site `files/authentik/homelab.yaml`, `tests/test_authentik_blueprint.py`, `overrides/navidrome.yml`, `edge/Caddyfile`, `edge/config.yml`, DNS.

Application-tier changes ship before the cutover, because the edge rollback restores only `edge.json`.

- [ ] Authentik blueprint:
  - Add a `forward_single` proxy provider whose external host is the music host.
  - Add an application for it, with a launch URL, bound to `family` and `admins`.
  - Add the provider to the embedded outpost's `providers`, keeping the existing one; the outpost only serves listed providers.
  - Remove the `family` binding from the base-host application.
- [ ] Update `tests/test_authentik_blueprint.py`: two proxy providers, the base-host app bound to `admins` only, and the music app bound to `admins` and `family`.
- [ ] Navidrome override:
  - Set `ND_BASEURL` to empty. The platform module sets `/navidrome`; the site override wins.
  - Set `ND_EXTAUTH_USERHEADER` to `X-Authentik-Username`, which both proxies supply.
  - Set `ND_EXTAUTH_LOGOUTURL` to the music host's `/outpost.goauthentik.io/sign_out`, because sessions are per host.
- [ ] Caddyfile:
  - Route the music host to Navidrome behind the gate.
  - On the base host, keep the ungated `/navidrome/rest/*` handle, adding `uri strip_prefix /navidrome`.
  - Redirect the other `/navidrome*` paths to the music host *before* the gate. The matcher must exclude `/navidrome/rest/*`, because `redir` sorts ahead of `handle`.
- [ ] Add the music host's tunnel ingress and DNS record if family use it remotely.
- [ ] Merge and let the release apply, then verify:
  - **Family account:** auto-login to Navidrome on the music host works. Every admin application shows Authentik's access-denied page, and its backend receives no request.
  - **Admin account:** reaches every application.
  - **Phone apps:** reconfigured to the music host; sync, streaming and cover art work.
  - **Forged headers:** a client-supplied `X-Authentik-Username` on that API path, and on the music host without a session, does not log anyone in.

**Exit:** under Caddy, family access is decided by Authentik alone. The Caddy `admins_only` checks are still in place but now duplicate Authentik's decision.

### Task 3: Write the Traefik site and rehearse its security behaviour

**Files:** private site `edge/traefik.yml`, `overrides/traefik.yml`, `site.yaml`, `tests/test_site_boundary.py`, a test for `edge/traefik.yml`, and `traefik-cutover.sh`. Keep the Caddy files for the revert.

- [ ] Translate every Caddy site block into routers, services and middlewares. Set explicit `priority` where rules overlap. Carry over:
  - **Listeners per host:** the base host on `lan-tls` serves only the VPN subscription paths and denies the rest; browser apps join only `tailnet` and `tunnel`.
    - Every router on `tunnel` sets `tls`. The `tunnel` entry point has no TLS of its own, and cloudflared connects over HTTPS.
    - Only intended public routers join `tunnel`.
  - **`:80` (`lan`):**
    - accepts any Host;
    - proxies `/jellyfin*`;
    - denies `/overseerr*`;
    - redirects admin paths with the same exclusions as Caddy;
    - denies everything else.
  - **Identity headers:** strip `Remote-User`, `X-Authentik-*`, `X-Real-IP` and `True-Client-IP` on every router, ungated ones included, before forward auth. Navidrome's user header comes from Authentik's response.
  - **Gating:** the Authentik outpost path on every gated host, ahead of the gated routes; the music host's ungated `/rest/` and VPN subscription paths.
  - **Paths:**
    - trailing-slash redirects for the apps that have them;
    - `stripPrefix` where Caddy uses `handle_path`; Traefik's `X-Forwarded-Prefix`, which it sets automatically, is what AudioMuse needs;
    - AudioMuse's and the solver host's extra forwarded headers.
  - **Responses:**
    - HSTS (`stsSeconds`, `stsIncludeSubdomains`);
    - `compress` on the hosts where Caddy compresses, but not on Plex or Jellyfin;
    - no buffering middleware on Plex, Jellyfin or `/dl/*`;
    - `responseForwarding.flushInterval: -1ms` on those services.
  - **Plex hosts:** Plex plus `/dl/*` to `homelab-mcp`.
  - **Certificates:** request exact names, or wildcards that the rehearsal proves.
- [ ] In `overrides/traefik.yml`:
  - Publish the same host listeners as Caddy (443/9443/80/8443 → 4443/5443/8080/8443).
  - Keep Caddy's fixed `homelab_net` `ipv4_address`, which Authentik's trusted proxies, Navidrome's trusted sources and the qBittorrent bypass all trust.
- [ ] In `site.yaml`, select `traefik` instead of `caddy` and rename the `exposed` entries from `caddy:` to `traefik:`. Port the port and `exposed` assertions in `tests/test_site_boundary.py`.
- [ ] Port the assertions of `tests/test_caddy_auth.py` to the Traefik file, so site CI tests the edge that will run.
- [ ] Render the candidate. Check that its `edge.json` contains exactly `traefik` and `cloudflared`, the expected binds and address, the secret file path, and no Caddy. Record the platform, site and plugin commits and the file's SHA-256.
- [ ] On a disposable Docker host with real Authentik, the candidate routes and synthetic names, check:
  - **Access:** the browser flow for anonymous, family, admin, unknown-user and no-group accounts, with zero backend hits on every denied path.
  - **Headers:** forged identity headers never reach a backend, and forwarded identity headers on ungated paths are stripped too.
  - **Exposure:** no private route answers on `tunnel`, `lan` or `lan-tls`.
  - **Tunnel TLS:** an HTTPS request carrying each tunnel host's SNI gets that host's certificate.
  - **Fail closed:** gated routes fail when Authentik is down.
  - **Certificates:** the rehearsal host has no Cloudflare token, so use Traefik's default self-signed certificate there. Issuance, RSA keys, the X1 chain and `acme.json` at mode 0600 are all checked live in Task 4.
  - **Swap and rollback (G10):** start from a working Caddy, apply an unhealthy Traefik model, and confirm that the updater restores Caddy, its listeners and its fixed address. Then apply the healthy model. The host runs Compose 5.1.4; earlier source checks covered only v2 releases, so this proves the orphan ordering on the production version.
- [ ] Write the private `traefik-cutover.sh`, run over `ssh -t`. It replaces the retired d6 probes, which named the Caddy container and its admin port. Stages:
  - `preflight`: tooling flag, timer state, token file readable, and `${APP_DATA_DIR}/traefik/acme` created with the module's PUID:PGID.
  - `apply`: compare the current release's `edge.json` digest with the approved one, then start the updater once.
  - `verify`: wait until production certificates exist, since `/healthz` is green before issuance and the updater won't roll back. Then:
    - send representative requests per route class;
    - assert that the Traefik container holds the fixed address;
    - check HSTS;
    - confirm that admin paths are denied on `lan` and `lan-tls`.
  - `close`: restart the timer.
- [ ] Prepare an unmerged revert PR that selects Caddy again.

**Exit:** the site PR passes CI, the rehearsal passes, and the revert PR is ready.

### Task 4: Cut over

- [ ] Run `traefik-cutover.sh preflight`. Stop `homelab-edge-update.timer` and confirm the service is inactive.
- [ ] Merge the site PR. Wait for the signed release to become current, then run `apply`.
  - If the updater reports a rollback, confirm that Caddy serves again, then fix forward with a new signed release.
  - Don't re-run a rolled-back release.
- [ ] Run `verify` from tailnet, LAN and tunnel clients, then check by hand:
  - browser login, callback and logout through the tunnel;
  - family versus admin boundaries with real accounts, and Navidrome auto-login for a family account;
  - the qBittorrent UI loads with no qBittorrent login;
  - a browser-trusted production certificate on every host;
  - each Plex host serves an RSA certificate whose chain reaches ISRG Root X1 (`openssl s_client -showcerts`);
  - Plex desktop playback through one of the custom server URLs;
  - Jellyfin and Plex streaming.
- [ ] If Traefik is healthy but anything is wrong, merge the revert PR, wait for its release, and run `apply` again. Keep the timer stopped until the edge is verified, then run `close`.

**Exit:** Traefik serves every route correctly, or the signed revert has restored Caddy.

### Task 5: Migrate setup

Setup on `main` has no Authentik wiring and no GATE-1 step. That groundwork was removed on purpose and archived under the `archive/wizard-*` tags. Rebuilding GATE-1 for Traefik (the transient `traefik/whoami` probe in 0020 §7, with its G9 pin check) and generating a per-host Authentik layout belong to the wizard's gate rebuild, not this migration.

**Files:** `setup/config/phases.json`, `setup/README.md`, setup tests and fixtures.

- [ ] Rename the `caddy` phase and service to `traefik`. Keep the engine's guard that holds any phase containing cloudflared until GATE-1 passes (`setup/engine/local.py`).
- [ ] Update the README and the setup tests and fixtures for the rename.
- [ ] Run `nox -s wizard`, `nox -s wizard_e2e` and `scripts/dev/gate --fast`.

**Exit:** setup selects Traefik as its edge proxy, and Caddy is no longer named in `setup/`.

### Task 6: Retire Caddy (7 days after a verified cutover)

- [ ] Mark 0020 Accepted with its gate evidence, without private values.
- [ ] Re-grep `caddy` case-insensitively across the repository. Remove or update every live reference in code, tests, fixtures, workflows, dependencies and user-facing docs. Keep historical decision records and plans. The last review found these:
  - **Code and configuration:**
    - `modules/edge/caddy.compose.yml` and `config/caddy/`;
    - the Caddy jobs in `.github/workflows/ci.yml` and `post-merge-build.yml`;
    - Caddy's entries in `.github/dependabot.yml` and `config/release/approved-ghcr-destinations.json`;
    - `NET_BIND_SERVICE` in `config/release/cap-allowlist.json` and `scripts/release/compose_policy.py`;
    - the `config/caddy/` path in `scripts/release/reconcile.py`;
    - `CADDY_IMAGE` in `.env.example`.
  - **Tests and fixtures:**
    - `tests/release`, `tests/ci` and `tests/host/test_edge_update.py`;
    - `tests/modules/site`, `owner_site`, `test_catalog.py` and `test_owner_site.py`;
    - rename `tests/modules/site_traefik`.
  - **Docs:** `README.md`, `AGENTS.md`, `docs/README.md`, `docs/architecture/overview.md`, `ingress.md`, `security.md`, `getting-started.md`, `configuration.md`, `troubleshooting.md`, `modules.md` and `module-migration.md`.
- [ ] Run `scripts/dev/gate`, then merge.
  - Verify that the platform release leaves the owner's Traefik edge healthy.
  - Open a site cleanup PR removing the Caddy files, override and tests.
- [ ] Keep the last signed Caddy bundle, the Caddy GHCR image and Caddy's certificate data until 90 days after the cutover.

**Exit:** the owner's host and new-user setup both run Traefik, and the platform no longer builds or selects Caddy.
