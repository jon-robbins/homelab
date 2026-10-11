# Boot orchestration

**Status: Planned/private.** This public repository does not ship a host boot
controller or a supported system service. A future controller may wait for
storage, networking, and rendered configuration before applying a stack, and
could report fields such as `last_run_failed`, `warnings[]`, and
`ingress_route_not_ready`; those are design vocabulary, not current output.

For current behavior, run [the off-host check](getting-started.md). Do not use
this page as an operator runbook.

