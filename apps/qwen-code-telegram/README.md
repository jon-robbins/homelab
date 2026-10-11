# qwen-code-telegram

Private homelab Telegram assistant leveraging Qwen Code's native Telegram channel integration, served by local vLLM (`qwen-serve`) with role-scoped Model Context Protocol (MCP) bridges.

## Architecture & Dual-Role Model

The stack runs two distinct, isolated Telegram bot roles to ensure defense-in-depth:

```
                  ┌─────────────────────────────────────────────────────────────┐
                  │                         Telegram API                        │
                  └──────────────┬───────────────────────────────┬──────────────┘
                                 │                               │
                      (Trusted User Channel)           (Admin / Owner Channel)
                                 │                               │
                                 ▼                               ▼
                 ┌──────────────────────────────┐┌──────────────────────────────┐
                 │      qwen-code-telegram      ││   qwen-code-telegram-owner   │
                 │   (ROLE: trusted, port: 0)   ││    (ROLE: owner, port: 0)    │
                 └──────────────┬───────────────┘└───────────────┬──────────────┘
                                │                                │
                      (Bearer: TELEGRAM_MCP_*)         (Bearer: TELEGRAM_OWNER_*)
                                │                                │
                                ▼                                ▼
                 ┌──────────────────────────────┐┌──────────────────────────────┐
                 │     homelab-mcp-telegram     ││  homelab-mcp-telegram-owner  │
                 │   (Profile: trusted, ro)     ││      (Profile: full, ro)     │
                 └──────────────┬───────────────┘└───────────────┬──────────────┘
                                │                                │
                                └───────────────┬────────────────┘
                                                │
                                                ▼
                         ┌──────────────────────────────────────────────┐
                         │               Homelab Services               │
                         │   (qBittorrent, Radarr, Sonarr, Prowlarr,    │
                         │    Overseerr, Plex, Autobrr, Bazarr)         │
                         └──────────────────────────────────────────────┘
```

| Component | Service | Role / Profile | Capabilities & Tool Surface |
|---|---|---|---|
| **User Bot** | `qwen-code-telegram` | `trusted` | Read tools (`read`), trusted media acquisition (`trusted-acquisition`), and trusted single-item controls (`trusted-single-control`). Paired with `homelab-mcp-telegram`. |
| **Owner Bot** | `qwen-code-telegram-owner` | `owner` / `full` | Full tool surface including bulk torrent management (`owner-mutation`), service maintenance (`owner-admin`), and indexer/filter controls. Paired with `homelab-mcp-telegram-owner`. |
| **Local LLM** | `qwen-serve` | Serving Backend | Local vLLM Qwen 2.5 32B Coder inference endpoint serving both bots over `llm_net`. |

## Security Boundaries & Hardening

| Boundary | Enforcement Mechanism |
|---|---|
| **Slash Command Lockdown** | `/etc/qwen-code/settings.json` disables all built-in/bundled slash commands. Only `/help`, `/clear`, `/status`, and `/compress` are allowed. Unsafe commands (e.g. `/compress-fast`, `/shell`) are disabled. |
| **Tool Execution Lockdown** | Native tools disabled in system policy (`tools.disabled`, `permissions.deny`, `tools.computerUse.enabled=false`). All actions must go through the scoped MCP bridge. |
| **Role-Scoped Capability Filtering** | `apps/qwen-code-telegram/config/mcp-role-tools.json` defines authoritative capability mappings. `homelab-mcp` rejects any tool call not permitted by the active role profile (`HOMELAB_MCP_TOOL_PROFILE=trusted|full`). |
| **Context Management & Compaction** | `generationConfig.contextWindowSize = 65536` with auto-compaction threshold `0.5` (~32.5k tokens). `/compress` acts as manual compaction recovery path. |
| **Credential & Token Isolation** | `homelab-mcp-telegram` mounts `TELEGRAM_MCP_API_TOKEN`; `homelab-mcp-telegram-owner` mounts `TELEGRAM_OWNER_MCP_API_TOKEN`. Neither bot or bridge mounts the primary `MCP_API_TOKEN`. |
| **Zero Inbound Ingress** | Both Telegram bot containers publish zero ports (`ports: 0`); all updates flow via outbound HTTPS long-polling to Telegram servers. |
| **Filesystem Isolation** | Bot runtime executes as non-root (`user: 10001:10001`) with read-only root filesystems where possible, tmpfs mounts for ephemeral state, and dedicated named volumes (`qwen-code-telegram-workspace`, `qwen-code-telegram-owner-workspace`). Host filesystem binds are strictly forbidden. |
| **Response & Evidence Contract** | LLM responses calling tools must produce a strict 3-line evidence ledger (`Result:`, `Verified:`, `Checked:`) with ISO-8601 timestamps. Credentials and internal system paths are strictly redacted. |

## Canonical Role & Capability Registry

Tool access is governed by `apps/qwen-code-telegram/config/mcp-role-tools.json`:

```json
{
  "capabilities": {
    "arr_lookup": "read",
    "radarr_release_grab": "trusted-acquisition",
    "arr_queue_remove": "trusted-single-control",
    "qbt_torrents_pause": "owner-mutation",
    "autobrr_irc_restart": "owner-admin"
  },
  "roles": {
    "trusted": [ "arr_lookup", "radarr_release_grab", "arr_queue_remove", ... ],
    "owner": [ "arr_lookup", "radarr_release_grab", "arr_queue_remove", "qbt_torrents_pause", "autobrr_irc_restart", ... ]
  }
}
```

### Capability Tiers:
1. `read`: Safe, idempotent data retrieval (service health, torrent listing, media lookup).
2. `trusted-acquisition`: Single-item searches and acquisition grabs bound to resolved indexers and release metadata.
3. `trusted-single-control`: Exact-ID single item deletion/cancellation and single torrent force-starts.
4. `owner-mutation`: Bulk torrent tagging, bulk pause/resume, seed-slot allocations, and filter modifications.
5. `owner-admin`: Administrative daemon restarts and offload pipeline triggers.

## Compose Profile Activation

All 5 Telegram and LLM services are grouped under `profiles: ["qwen-serve"]`.

```bash
# Start all 5 services together:
docker compose --profile qwen-serve up -d --build \
  qwen-serve homelab-mcp-telegram homelab-mcp-telegram-owner \
  qwen-code-telegram qwen-code-telegram-owner

# Verify default unscoped compose config excludes all 5 services:
docker compose config | jq '.services | keys'
```

## Required Secrets & Environment Variables

| Secret / Env Var | Target Service | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `qwen-code-telegram` | Bot token from `@BotFather` for trusted user bot |
| `TELEGRAM_OWNER_BOT_TOKEN` | `qwen-code-telegram-owner` | Bot token for admin/owner bot |
| `TELEGRAM_MCP_API_TOKEN` | `homelab-mcp-telegram`, `qwen-code-telegram` | Dedicated bearer token for trusted MCP communication |
| `TELEGRAM_OWNER_MCP_API_TOKEN` | `homelab-mcp-telegram-owner`, `qwen-code-telegram-owner` | Dedicated bearer token for owner MCP communication |
| `VLLM_API_KEY` | `qwen-serve`, bot containers | Local vLLM authentication key |
| `PLEX_TOKEN` | MCP bridge containers | Plex API token for library search & visibility diagnostics |
| `QWEN_CODE_TELEGRAM_ALLOWED_CHAT_IDS` | `qwen-code-telegram` | Comma-separated list of allowed user/chat IDs |
| `QWEN_CODE_TELEGRAM_OWNER_ALLOWED_CHAT_IDS` | `qwen-code-telegram-owner` | Comma-separated list of allowed admin/owner user IDs |

## Testing & Verification

```bash
# 1. Run unit & integration test suites:
pytest tests/integration/ test_mcp_envelopes.py test_telegram_cases.py test_telegram_fixture_state.py \
  apps/qwen-code-telegram/tests/ apps/homelab-mcp/src/homelab_mcp/tests/

# 2. Run structural & policy checks:
bash apps/qwen-code-telegram/tests/test_system_policy.sh
bash apps/qwen-code-telegram/tests/test_entrypoint.sh
bash apps/qwen-code-telegram/tests/test_role_render.sh
bash tests/compose/test_qwen_telegram_structure.sh

# 3. Run autonomous live smoke runner (requires an external Telethon session file):
python3 tests/integration/telegram_role_smoke.py --live --user --timeout-seconds 90
```
