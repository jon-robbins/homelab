# Secrets management (Infisical Phase 1)

[← Back to README](../README.md) · [Configuration](configuration.md)

Phase 1 moves runtime credentials from plaintext `.env` into [Infisical Cloud](https://infisical.com). `.env` remains the **non-secret configuration contract** (paths, domains, feature flags). Managed secret **names** stay in `.env.example` as blank stubs so Compose, tests, and agents know the contract; values live in Infisical. Non-managed environment variables are unchanged and are never fetched merely because they exist in an Infisical folder.

## Architecture

```text
Infisical Cloud
project: homelab
environment: prod
domain-scoped secret paths
          |
          | Universal Auth (REST API)
          | read-only runtime identity (homelab-runtime)
          v
scripts/lib/infisical_fetch.py (stdlib REST client, zero dependencies)
          |
          +-----------------------------+
          |                             |
          v                             v
homelab-compose.py              host operation scripts
(all Phase-1 paths)             (scoped paths only)
          |                             |
          v                             v
docker compose                  ephemeral env (no /run/secrets on host)
          |
          | per-service secret grants
          v
/run/secrets/<NAME>
          +-- caddy, overseerr, homelab-mcp, media-agent, workers
```

### Two least-privilege layers

1. **Machine identity** — what this **host** may retrieve from Infisical (Phase 1: one read-only `homelab-runtime` identity with access to required paths; splitting identities is future work).
2. **Compose secret grants** — what each **container** receives under `/run/secrets/<NAME>` (explicit per service in compose fragments).

Containers read file-mounted secrets (`get_secret()` in app code). Explicit Compose secrets grants deliver only the requested values to `/run/secrets/<NAME>`. Host ops receive scoped ephemeral environment through `scripts/ops/run-with-secrets.sh`. Do not fake `/run/secrets` on the host.

## Threat model (honest limitations)

This removes the large plaintext credential collection from the homelab filesystem and makes Infisical authoritative. It **does not** protect secrets from a live root-level compromise. The host still needs one bootstrap credential to authenticate to Infisical; a sufficiently privileged attacker on an authorized machine can obtain secrets.

**Improvements:** secrets off-host; `.env` no longer a credential dump; per-service Compose grants; domain-scoped host fetches; centralized rotation/revocation; secrets not copied into Compose `environment:`.

**Caveat — Infisical CLI cache:** the Infisical CLI may retain tokens or local vault state on disk for offline/retry behavior. Phase 1 does not claim “no persistent secrets on the host”; it claims managed credentials are no longer stored as a plaintext dump in `.env`. Treat the bootstrap file and any CLI cache under the operator home as sensitive.

**Future work:** TPM/systemd protection for the bootstrap credential; split machine identities.

**Out of scope (Phase 1):** cloudflared tunnel credential JSON under `data/cloudflared/credentials` (significant follow-up); Plex tokens in Plex state; application databases that store their own API keys.

## Infisical path taxonomy (`prod`)

| Path | Keys |
|------|------|
| `/network` | `CLOUDFLARE_TOKEN` |
| `/media` | `TMDB_API_KEY` |
| `/arr` | `SONARR_API_KEY`, `RADARR_API_KEY`, `PROWLARR_API_KEY`, `OVERSEERR_API_KEY`, `AUTOBRR_API_KEY` |
| `/qbt` | `QBITTORRENT_USERNAME`, `QBITTORRENT_PASSWORD` |
| `/llm` | `GEMINI_API_KEY`, `MCP_API_TOKEN`, `MEDIA_AGENT_TOKEN`, `HOMELAB_AGENT_API_TOKEN`, `VLLM_API_KEY` |

Key names must be unique across folders. Authoritative catalog: `scripts/lib/managed_secrets.py`.

## Bootstrap credential

Path (not in repo): `${HOME}/.config/homelab/infisical.env`

```text
INFISICAL_CLIENT_ID=
INFISICAL_CLIENT_SECRET=
INFISICAL_PROJECT_ID=
INFISICAL_ENV=prod

# optional, for EU Cloud / self-hosted
INFISICAL_API_URL=
```

Create with mode `0600` (or `0400`). File must be owned by the runtime user with **no group or other permissions**. Never commit this file.

This file is only the host bootstrap credential for Infisical Universal Auth.
Do not add application secrets such as `MCP_API_TOKEN`, Arr API keys,
qBittorrent credentials, or `CLOUDFLARE_TOKEN` to `infisical.env`; those
values belong in the Infisical project paths above.

## Manual Infisical setup

1. Install the [Infisical CLI](https://infisical.com/docs/cli/overview) on the runtime host (optional — the Python scripts use stdlib REST, no CLI dependency).
2. Create project `homelab`, environment `prod`, and folders `/network`, `/media`, `/arr`, `/qbt`, `/llm`.
3. Create machine identity `homelab-runtime` with Universal Auth; grant **read-only** access to those paths.
4. Create machine identity `homelab-seed` with Universal Auth; grant **write** access to the same paths (session-only — do not store its credentials on disk).
5. Populate secrets (UI, or seed script below).
6. Write the **read-only** bootstrap file at the path above (`chmod 0600`) using `homelab-runtime` Client ID/Secret + Project ID.
7. Verify: `scripts/ops/homelab-compose.py config --quiet` (from production tree `${HOMELAB_ROOT}` after merge). This authenticates to Infisical, validates required keys, and statically renders Compose without changing runtime state.

## Stock Agent host install

The unattended runtime path uses the stock Infisical Agent as a host-level
systemd service. It renders one file per managed secret under
`${HOME}/.config/homelab/secrets/` and leaves the existing Compose
`/run/secrets/<NAME>` contract unchanged.

Run this one-time setup from the runtime host checkout:

```bash
cd "${HOMELAB_ROOT}"
sudo ./scripts/ops/install-infisical-agent.sh
```

The installer:

- validates `${HOME}/.config/homelab/infisical.env` has owner-only
  permissions and the required bootstrap fields;
- writes the Universal Auth client ID and client secret to
  `/etc/infisical-agent/auth/`;
- installs `/etc/infisical-agent/config.yaml` from the checked-in stock
  template with runtime secret destinations; and
- installs `config/systemd/homelab-infisical-agent.service` to
  `/etc/systemd/system/` without starting it automatically.

After the installer finishes, run the printed operator commands:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now homelab-infisical-agent.service
sudo systemctl status homelab-infisical-agent.service
```

Verify the first render before any Compose mutation:

```bash
ls -l "${HOME}/.config/homelab/secrets"
stat -c '%a %n' "${HOME}/.config/homelab/secrets"/*
```

The directory should exist with mode `0700`, each rendered secret file should
be present with owner-only permissions (`0600`), and no secret values should be printed
to the terminal or logs during setup.

## Infisical Agent Pilot Gate and Telegram Sequencing

Runtime migration to the Infisical Agent follows a strict verification gate.

1. **Pilot cohort:**
   - Simple read-only consumer: `caddy` (`CLOUDFLARE_TOKEN`)
   - Normal consumer: `overseerr` (`OVERSEERR_API_KEY`)
2. **Pilot verification gates (explicit approval required):**
   - Initial render: validates permissions (`0700` directory, `0600` files) and key presence.
   - Outage simulation: verifies boot and services continue when Infisical is offline using persistent files.
   - One-key rotation & scoped reload: verifies `scripts/ops/infisical-agent-reload.py` recreates only affected consumers (`--no-deps --force-recreate`).
   - Log inspection: confirms zero secret values leaked in agent or service logs.
   - Rollback: verifies Infisical secret version history restore triggers automatic reload.
3. **Telegram migration:**
   Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_MCP_API_TOKEN`) is migrated to Infisical Agent file-backed delivery (`${HOME}/.config/homelab/secrets/TELEGRAM_BOT_TOKEN` and `TELEGRAM_MCP_API_TOKEN`). Both secrets are scoped under `/llm` with dedicated grants in `compose/llm.yml` under the `qwen-serve` profile (`homelab-mcp-telegram` and `qwen-code-telegram`), preserving token isolation (no primary MCP token inheritance) and zero-port ingress.

## Seed from `.env` (one-shot write identity)

Use `homelab-seed` only for the process that uploads secrets. Do **not** put seed credentials in `~/.config/homelab/infisical.env`.

From `${HOMELAB_ROOT}`:

```bash
# Dry-run: nonempty managed key names only
./scripts/ops/infisical_seed_from_env.py

INFISICAL_SEED_CLIENT_ID=… INFISICAL_SEED_CLIENT_SECRET=… \
  ./scripts/ops/infisical_seed_from_env.py --apply

./scripts/ops/homelab-compose.py config --quiet
# staged recreate per First rollout below…

./scripts/ops/infisical_seed_from_env.py --clear-env
./scripts/ops/audit-local-env-secrets.sh
```

`--clear-env` verifies Infisical via the read bootstrap before blanking managed values in `.env`.

## Host vs container delivery

| Context | Mechanism | Helper |
|---------|-----------|--------|
| Unattended stack / deploy | Infisical Agent → `${HOME}/.config/homelab/secrets/KEY` → Compose file secrets | Direct `docker compose` |
| Compatibility / transition deploy | Infisical → env → Compose secrets | `scripts/ops/homelab-compose.py` (deprecated after pilot gate) |
| Host ops (DNS, qBT, Arr helpers) | Scoped Infisical secret values only (`INFISICAL_TOKEN` scrubbed before the command) | `scripts/ops/run-with-secrets.sh` |
| Compose services | `/run/secrets/<NAME>` (file-backed from Agent) | Explicit Compose `secrets:` grants |
| CI (validate/build only) | `CI_PLACEHOLDER` in a scoped subprocess | `export_ci_secret_placeholders` — never exported into live `e2e`/`nightly` mutations |

## Local-app API-key mirror caveat

Arr/Overseerr/autobrr API keys remain in each application's persisted config on disk. Infisical removes only the **redundant plaintext copy** in `.env`. Rotating a key requires updating both Infisical and the application UI/config where applicable.

**qBittorrent WebUI password:** rotating `QBITTORRENT_PASSWORD` requires updating the password **inside qBittorrent** and the matching value in Infisical (`/qbt`). Updating only Infisical leaves WebUI auth broken until both match.

## First rollout (manual / staged)

Do **not** rely on nightly deploy for the first Infisical cutover. Nightly runs a full `docker compose up -d --build` and recreates the whole stack at once. For the first production rollout:

1. Install Infisical CLI; create project + `homelab-runtime` identity (read-only).
2. Populate Infisical paths with values.
3. Create bootstrap file (`0600`).
4. Install and enable Agent: `sudo ./scripts/ops/install-infisical-agent.sh`.
5. Execute the pilot gate.
6. Recreate remaining consumers in stages.
7. Clear migrated values from local `.env` (names stay; values blank).
8. Run the local audit (below).
9. Rotate credentials that were historically exposed or weak.

Only after a successful staged rollout and pilot gate should nightly deploys be trusted as the routine path.

**Deploy note:** Nightly Infisical preflight uses the candidate `dev` checkout (`GITHUB_WORKSPACE`) with `--env-file` pointing at production `.env` and `HOMELAB_SECRET_DIR=${HOME}/.config/homelab/secrets`; production deploy mutates `${HOMELAB_ROOT}`. Do not deploy from the feature worktree.

## Local `.env` audit

After clearing migrated values, confirm `.env` holds no secret values (names only in output):

```bash
./scripts/ops/audit-local-env-secrets.sh
# or: ./scripts/ops/audit-local-env-secrets.sh /path/to/.env
```

Exit 0 when clean; exit 1 listing offending key names only.

## Rollback & Outage Behavior

### Infisical Outage
If Infisical Cloud is temporarily unavailable or network connectivity fails:
- The Infisical Agent logs connection warnings and enters an exponential retry loop.
- Existing rendered secret files under `${HOME}/.config/homelab/secrets/` are **never deleted or emptied**.
- Running containers and reboot bring-up via `homelab-boot-orchestrator.sh` continue operating normally using the persisted files.

### Bad Secret Value Rollback (Infisical Version History)
If a secret value is updated incorrectly in Infisical:
1. Navigate to the key in Infisical Cloud and restore the previous version from Secret Version History.
2. The Agent detects the change on its next poll cycle, renders the restored value, and triggers `infisical-agent-reload.py --key <KEY>` to recreate the consuming service automatically.

### Full Stack Rollback
If the Agent configuration or a code revision fails:
1. Temporarily restore secret values to `.env` (or checkout the previous commit).
2. Use plain `docker compose` or the previous deploy path until the issue is resolved.
3. Clear `.env` values again after recovery and re-run the audit.

## Related files

- `scripts/lib/managed_secrets.py` — authoritative managed secret catalog
- `scripts/lib/infisical_fetch.py` — Infisical runtime client, bootstrap, Universal Auth, path-scoped run
- `scripts/ops/install-infisical-agent.sh` — Agent host installer
- `scripts/ops/infisical-agent-reload.py` — scoped consumer reload hook
- `scripts/ops/homelab-compose.py` — root Compose entrypoint (thin `.sh` alias also available)
- `scripts/ops/run-with-secrets.sh` — host-scoped launcher
- `scripts/ops/infisical_seed_from_env.py` — one-shot seed from `.env` (session write identity)
- Pilot runbook and design details remain in the private operations repository.
- Phase 1 spec: `docs/superpowers/specs/2026-08-10-infisical-secrets-design.md`

### `source_nonsecret_env` syntax contract

`source_nonsecret_env` is a small `KEY=value` parser (not a full Compose `.env` loader). It supports unquoted `NAME=value` lines and skips comments/blank lines. It does **not** implement Compose quoting, escapes, or interpolation. Keep non-secret `.env` assignments in that simple form (as in `.env.example`).
