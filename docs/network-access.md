# Network access

**Status: Current.** The Compose model uses the `homelab_net` bridge for
internal service discovery. Host ports and external URLs are deployment
inputs, not public guarantees.

| Service | Internal endpoint |
| --- | --- |
| autobrr | `http://autobrr:7474` |
| bazarr | `http://bazarr:6767` |
| qbittorrent | `http://qbittorrent:8080` |
| homelab-mcp | `http://homelab-mcp:8090` |
| homelab-agent | `http://homelab-agent:8091` |
| sonarr / radarr / prowlarr | container DNS plus their configured ports |

Use service names from inside the bridge network; do not assume localhost.
Check the rendered configuration with [the public check](getting-started.md).
External ingress, VPN, and a host controller are **Planned/private**.

