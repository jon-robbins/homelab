# Homelab Agent API

API-only agent service for the homelab: an OpenAI-style chat endpoint in front
of the in-stack `qwen-serve` vLLM backend (`http://qwen-serve:18020/v1` on
`homelab_net`) and the `homelab-mcp` tool surface. Clients send one chat
request; the service runs the model/tool loop server-side and returns the
final answer.

- Spec: `docs/superpowers/specs/2026-08-19-homelab-agent-api-design.md`
- Plan: `docs/superpowers/plans/2026-08-19-homelab-agent-api.md`

## Endpoints

| Endpoint | Auth | Notes |
|----------|------|-------|
| `GET /health` | none | Liveness probe used by the compose healthcheck. |
| `GET /v1/models` | bearer | Static model list from settings. |
| `POST /v1/chat/completions` | bearer | OpenAI-style chat. `stream: true` is rejected (not implemented this phase). |

Auth is a bearer token. In compose the token is mounted via `secrets:`
(`/run/secrets/HOMELAB_AGENT_API_TOKEN`); the env var wins over the file when
both are set.

## Agent loop behavior

1. Normal completion → `200` with the final assistant message; audit record
   with `status: "ok"`.
2. Model requests tools → each tool is checked against the allowlist, executed
   via `homelab-mcp`, and the result is appended to the conversation.
3. Loop exhausts `HOMELAB_AGENT_MAX_TOOL_STEPS` → `200` with the best partial
   answer, `finish_reason: "length"`, and `homelab_agent.status:
   "loop_exhausted"` metadata; audit record with `status: "loop_exhausted"`.
4. Failures (allowlist denial, backend errors, malformed tool arguments) →
   explicit `4xx`/`5xx` and an audit record with `status: "error"`,
   `error`, and `status_code`. Every failure type leaves a trail.
5. Backend failures map to explicit codes: model/MCP timeout → `504`,
   connection failure or bad upstream status → `502`.
6. Transient transport errors (connect resets, timeouts) are retried once in
   both the model and MCP clients before surfacing.

## Limits

| Setting (env var) | Default | Effect |
|-------------------|---------|--------|
| `HOMELAB_AGENT_MAX_MESSAGES` | 100 | Requests with more messages get `422`. |
| `HOMELAB_AGENT_MAX_MESSAGE_CHARS` | 65536 | Per-message content cap, `422` beyond. |
| `HOMELAB_AGENT_MAX_TOOL_OUTPUT_CHARS` | 8192 | Tool output truncated before re-entering the conversation (untrusted input). |
| `HOMELAB_AGENT_MAX_RESPONSE_CHARS` | 65536 | Final assistant content truncated. |
| `HOMELAB_AGENT_MAX_TOOL_STEPS` | 6 | Model/tool loop iterations before exhaustion. |

Tool output truncation is recorded in the audit entry (`truncated: true`).

## Development

```bash
cd apps/homelab-agent
uv sync --extra dev
./.venv/bin/pytest -q
./.venv/bin/ruff check app tests
```

## Compose

The service lives in `compose/llm.yml` (`homelab-agent`), on `homelab_net`
only, no host port. It reaches the in-stack `qwen-serve` vLLM backend at
`http://qwen-serve:18020/v1` (the compose default; override only for
maintenance) and `homelab-mcp` at `http://homelab-mcp:8090`. Audit logs are
written to `${APP_DATA_DIR}/homelab-agent:/data`. The old host-side `~/qwen`
deployment (and its `host.docker.internal` rollback plumbing) was retired at
the Stage B cutover (2026-08-20).
