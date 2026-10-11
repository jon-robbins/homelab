# Architecture notes

**Status: Current.** Use [the architecture overview](architecture/overview.md)
for the supported public model. Compose modules feed workers and the MCP core;
private extensions connect only through their interface.

A signed release feed and host controller are **Planned** and must not be read
as shipped behavior. See [threat model](architecture/threat-model.md).

