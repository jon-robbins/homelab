# Router

`router` turns natural-language media requests into deterministic actions.

- `parser.py`: intent classification and action parsing.
- `router_orchestrator.py`: staged pipeline for parse, hydrate, plan, execute, and render.
- `router_policy.py`: policy paths and fallback rules.
- `router_runtime_helpers.py`: matching and pending-option helpers.
- `router_selection.py`: follow-up selection parsing and option IDs.
- `router_state.py`: persisted session state.
- `router_contracts.py`: provider protocol used by the orchestrator.
- `formatting.py`: user-facing response text.

The router may call actions, but actions should not depend on router orchestration.
