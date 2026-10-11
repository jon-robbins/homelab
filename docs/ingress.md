# Ingress

**Status: Current boundary; deployment details are planned/private.** The
public repository provides a Caddy template and Compose network definitions.
It does not promise WAN, LAN, VPN, tunnel, port-forwarding, or DNS behavior.

Use [Caddy guide](caddy-guide.md) for route directives and [network access](network-access.md)
for container-to-container examples. A private overlay must supply hostnames,
certificates, firewall policy, and any tunnel configuration. Do not infer those
values from this repository. A host controller for applying that policy is
**Planned**.

