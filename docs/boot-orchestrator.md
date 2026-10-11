# Boot orchestration

**Status: Planned/private.** This public repository does not ship a host boot
controller or a supported system service. A future controller may wait for
storage, networking, and rendered configuration before applying a stack, and
could report fields such as `last_run_failed`, `warnings[]`, and
`ingress_route_not_ready`; those are design vocabulary, not current output.

For current behavior, run [the off-host check](getting-started.md). Do not use
this page as an operator runbook.


## Current boot ordering with the Infisical Agent

**Status: Current, if you use the Infisical Agent.** `homelab-infisical-agent.service` starts after the network is
online and renders secret files on its first run. Start services that read secrets only after their rendered files
exist and are non-empty; the stack fails closed on a missing file rather than starting with an empty secret.
