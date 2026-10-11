# Actions

`actions` contains deterministic use cases. These modules receive validated inputs, an `httpx.Client`, and `Settings`, then return plain dictionaries for API envelopes and router responses.

- `action_service.py`: validates action payloads and dispatches to the correct use case.
- `lookup.py`: Sonarr/Radarr metadata lookup and library membership checks.
- `download_options.py`: Sonarr/Radarr release option and grab flows.
- `prowlarr_flow.py`: direct Prowlarr search and grab flow.

Do not put conversational parsing or FastAPI route concerns here.
