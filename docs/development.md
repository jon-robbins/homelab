# Development

[← Back to README](../README.md)

Python workers, live-stack integration checks, and extending the media-agent service.

## Python Workers

The source of truth for packaged workers is `apps/homelab-workers` (`pyproject.toml`, package code, tests). **Retries and cleanup are handled by Sonarr/Radarr "Failed Download Handling"** (no dedicated worker container).

Install for local development:

```bash
cd apps/homelab-workers
python3 -m pip install -e ".[dev]"
```

Media-pipeline E2E (`media-pipeline-e2e` / `tests/integration/media-pipeline-e2e.sh`) is **retired** and is not a supported path. For Bazarr live checks, see [`tests/integration/README.md`](../tests/integration/README.md).

## Extend Media-Agent Capabilities

Each agent-callable capability is a small **action handler** under `apps/media-agent/app/actions/`. The LLM parser (`app/router/parser.py`) and the strict `/action` payload union (`app/models/actions.py`) are driven from the same registry, so a new action is one coherent unit of work.

### Where to add things

- `apps/media-agent/app/actions/<name>.py`
  - One module per action: Pydantic args model, `@register_action` handler with `run()` (and, if the router needs it, `run_for_router()`), `format_response`, and `selection_to_grab` for option lists.
- `apps/media-agent/app/actions/__init__.py`
  - Import the new module so the registry is populated at startup.
- `apps/media-agent/app/models/actions.py`
  - Add the action’s payload class and add it to the `ActionCall` union / discriminator.
- `apps/media-agent/app/router/parser.py` / `app/router/intent.py`
  - Usually no edits: the router schema and allowed-field map are built from `registry.all_handlers()`. Touch these only for special parsing or intent rules.
- `apps/media-agent/app/integrations/`
  - Low-level HTTP to Sonarr, Radarr, Prowlarr, qBittorrent, Ollama. Keep I/O here; business flow stays in `app/actions` or `app/services/`.
- `apps/media-agent/app/services/`
  - Reusable non-route helpers (release ranking, torrent-name matching, qB file selection).

### Fast extension checklist

1. Add a Pydantic model in `app/models/actions.py` and include it in `ActionCall`.
2. Create `app/actions/<your_action>.py` with `register_action`, `run`, and any router overrides. Import it from `app/actions/__init__.py`.
3. Add tests under `apps/media-agent/tests/api/` or `apps/media-agent/tests/unit/`.
4. Run:

```bash
cd apps/media-agent && python -m pytest -q
cd apps/media-agent && ruff check app tests
```
