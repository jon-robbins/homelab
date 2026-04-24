# Core

`core` contains shared definitions that other packages depend on.

- `config.py`: environment-backed settings and cached `get_settings()` access.
- `models.py`: public API models, router state models, and action-call models.
- `action_catalog.py`: canonical action registry used by routes, router parsing, and function listings.

Keep this package free of external HTTP calls and framework routing code.
