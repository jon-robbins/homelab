# Threat model

**Status: Current.** This describes the public repository boundary. It does not
claim protection for a private host or deployment.

The checkout contains generic Compose, pinned image references, application
code, tests, and synthetic defaults. It should not contain deployment
credentials, host-specific paths, or private routes. CI renders with temporary
sentinel secret files and runs static tests; it does not start the stack.

A private overlay and secret manager remain responsible for credentials,
network policy, storage, and ingress. Anyone who can control a runtime host may
be able to read runtime secrets. A signed release feed and host controller that
would add provenance and pull policy are **Planned**.

See [secrets](../secrets-management.md) and [operations](../operations.md).

