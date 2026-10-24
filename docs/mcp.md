# homelab-mcp

An MCP server that lets an agent session check and change the media stack without a pull request: list and move
files, delete into a trash, remove torrents or library items, act on Seerr requests, and diagnose autobrr. Why it
is shaped this way is in [0016](decisions/0016-agent-control-surface.md).

## Reaching it

The server listens on port 8090, bound to the host's Tailscale address. Clients use streamable HTTP at `/mcp`
with `Authorization: Bearer <token>`; `/health` needs no token. For Claude Code on a tailnet machine:

```bash
claude mcp add --transport http homelab http://<tailscale-host>:8090/mcp \
  --header "Authorization: Bearer <token>"
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MCP_API_TOKEN` (or `MCP_API_TOKEN_FILE`) | none | Bearer token; use the secret file |
| `HOMELAB_MCP_ENABLE_MUTATIONS` | `0` | `1` registers the tools that change anything |
| `HOMELAB_MCP_FILE_ROOTS` | `/downloads,/movies,/tv` | The only folders file tools may touch |
| `HOMELAB_MCP_AUDIT_PATH` | `/data/audit.jsonl` | Append-only record of every mutation attempt |
| `HOMELAB_MCP_EXTENSIONS` | empty | Comma-separated extension entry points to load |

The file roots must be mounted at the same paths qBittorrent, Radarr and Sonarr use, so paths an app reports can
be passed straight to a file tool.

## Changing things safely

- **Tier 1** (destructive: move, delete, empty trash, delete a torrent or library item, disable a filter): call the
  tool without `expected_action` first. The refusal's hint contains the exact string to send back, which binds the
  call to those paths or IDs.
- **Tier 2** (approve a request, restart IRC, enable a filter): pass `confirm=true`.
- `media_file_delete` moves the entry to `<root>/.homelab-trash/<UTC timestamp>__<name>`. Use `media_trash_list` to
  see it and move it back with `media_file_move` if needed; `media_trash_empty` purges entries older than N days.
- Prefer `arr_item_delete` or `qbt_torrents_delete` over a file delete when the app owns the item, so the app does
  not re-download or report it missing.

## Extensions

An extension is a Python package exposing a `homelab_mcp.extensions` entry point whose function receives an
`ExtensionContext`. It can register tools and roles, add release-grab guards, and append checks to
`autobrr_pipeline_diagnosis` through `autobrr_pipeline_checks`. A failing check is reported in the diagnosis
instead of failing it. Plugins build their own image `FROM` the published `homelab-mcp` image.

## Testing

`nox -s mcp` runs the unit tests. Before deploying an image change, run the Docker smoke test, which starts the
image as a non-root user with real media folders and drives it through an MCP client:

```bash
apps/homelab-mcp/scripts/docker-smoke.sh
```

`MCP_IMAGE`, `SMOKE_NETWORK` and `SMOKE_EXTRA` let a plugin run the same checks against its own image with stub
upstreams and extra checks.
