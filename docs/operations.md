# Operations

**Status: Current and planned.** Current CI validates source, renders Compose
with synthetic inputs, runs tests, scans the public boundary, and builds OCI
images off-host. It does not deploy or start containers.

The supported local gate is:

```bash
pre-commit run --all-files
nox -t no-docker
```

The signed release feed from N6 is current. A host controller for N7–N11 is
still **Planned**. Private site operations and runtime backups belong outside
this public repository.

CI installs pinned versions of pre-commit and nox. When adding a GitHub
Action, write its release tag first and run `pinact run`; pinact writes the
commit SHA and trailing version comment that Dependabot maintains.
