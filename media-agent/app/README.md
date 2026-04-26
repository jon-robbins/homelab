# Media-Agent App

Media-agent is the deterministic backend behind OpenClaw media requests. The app is organized by responsibility so new capabilities have a clear path from API contract to execution.

## Package map

- `core/`: shared configuration, Pydantic models, and the action catalog.
- `actions/`: deterministic use cases that execute validated actions.
- `router/`: conversational parsing, policy, session state, selection handling, and response formatting.
- `api/`: FastAPI transport concerns such as auth, dependency wiring, and response envelopes.
- `integrations/`: thin clients and helpers for external systems.
- `services/`: shared service-level utilities used by multiple packages.
- `main.py`: FastAPI composition root and route wiring.

## Extension path

1. Add or update request/response models in `core/models.py`.
2. Register the action in `core/action_catalog.py`.
3. Implement deterministic behavior in `actions/` or an integration helper.
4. Expose HTTP behavior through `main.py` / `api/` only when needed.
5. Teach the conversational router in `router/parser.py`, `router/router_policy.py`, and `router/formatting.py` how to parse, execute, and explain the action.
6. Add focused tests under `media-agent/tests/`.
