# Security

What the platform protects, what it relies on you to do, and a checklist for a host.

## Threat model

| Threat | Protection | Where |
|---|---|---|
| Someone pushes malicious code or config | releases come only from `release.yml` on `main` after a reviewed merge; the host verifies the cosign signature and the exact workflow identity | [0004](decisions/0004-signed-full-state-releases.md) |
| A compromised or swapped image | every image pinned by digest from allowlisted namespaces; first-party images attested | [0009](decisions/0009-image-trust.md) |
| A module escalates to the host | Compose policy: no privileged mode, no Docker socket, capabilities and GPU from allowlists, binds only under declared roots, host networking only in the edge tier | [0008](decisions/0008-compose-policy.md) |
| Secrets leak into git or logs | secrets are files rendered on the host; names are public, values never enter a release; public-readiness scans every tracked file | [0010](decisions/0010-secrets.md) |
| An internet-facing service is exploited | exposure allowlist, firewall rules Docker can't bypass, fast patching | [0015](decisions/0015-exposure-policy.md) (proposed), below |
| Data loss after a bad change | snapshot before every stateful change, nightly and off-site backups | [0012](decisions/0012-backups.md) |
| Nobody notices a failure | deadman pings with fail reasons | [0013](decisions/0013-deadman-alerting.md) |

Not protected: anyone with root on the host (or in the `docker` group, which is equivalent) can read every
secret and change every service. The defence is keeping the host unreachable.

## Exposure policy

Docker publishes ports with its own iptables rules, which run **before** UFW. A module that publishes
`"8096:8096"` listens on every interface, and any router port forward or UPnP mapping puts it on the internet,
whatever UFW says.

Rules ([0015](decisions/0015-exposure-policy.md)):

1. Publish ports on an explicit address: `"${TAILSCALE_IP}:443:443"` or a LAN address, not `"443:443"`.
2. Only the media server's streaming port and the torrent peer port may listen on all interfaces.
3. Admin UIs are reachable only from the LAN and Tailscale, through Caddy with a `remote_ip` guard
   ([ingress](ingress.md#caddy-routes)). Never port-forward an admin UI.
4. Add `DOCKER-USER` rules so that traffic from the WAN interface reaches only the allowlisted ports.
5. Turn UPnP off on the router.

What the catalog publishes today:

| Module | Published | Recommendation |
|---|---|---|
| caddy | `80`, `8443` on all interfaces; `443` on the Tailscale address | bind `80` and `8443` to the LAN address, or drop them |
| plex | `32400` on all interfaces | allowed (remote streaming); harden per [ingress](ingress.md#plex-remote-access) |
| jellyfin | `8096` on all interfaces | bind to the LAN address; use Tailscale remotely |
| qbittorrent | `51423` TCP and UDP on all interfaces | allowed (peer port); the web UI is not published |
| overseerr | `127.0.0.1:5055` | fine |
| cloudflared | host network, outbound only | fine |

### Firewall example

Replace `eth0` with the WAN-facing interface. `DOCKER-USER` sees traffic after Docker's NAT, so match the
container port with `conntrack --ctorigdstport`:

```bash
iptables -I DOCKER-USER -i eth0 -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
iptables -A DOCKER-USER -i eth0 -p tcp -m conntrack --ctorigdstport 32400 -j RETURN
iptables -A DOCKER-USER -i eth0 -p tcp -m conntrack --ctorigdstport 51423 -j RETURN
iptables -A DOCKER-USER -i eth0 -p udp -m conntrack --ctorigdstport 51423 -j RETURN
iptables -A DOCKER-USER -i eth0 -j DROP
```

Make the rules persistent (`iptables-persistent` or a systemd unit), and check from outside your network with a
port scan.

## Checklist

For a new host, and again after any change to modules, the router or the host:

- [ ] `ss -tlnpu` shows nothing on `0.0.0.0` or `[::]` that isn't on the allowlist above (or explained by
      something outside this project).
- [ ] Router: port forwards are only the allowlisted ports; UPnP off.
- [ ] `DOCKER-USER` drops WAN traffic to everything else.
- [ ] SSH: key-only (`PasswordAuthentication no`), ideally reachable only over Tailscale.
- [ ] No unused login accounts with a shell.
- [ ] Plex: secure connections required; no unauthenticated networks.
- [ ] Every admin UI has its own authentication turned on, even behind Caddy.
- [ ] `CLOUDFLARE_TOKEN` is limited to DNS edits on one zone.
- [ ] Your GitHub account has 2FA; the platform repo's `main` ruleset has no bypass.
- [ ] healthchecks.io alerts reach you (send a test fail ping).
- [ ] The latest backup restores.

## If you suspect a compromise

1. Close the router port forward for the affected service, then stop the service.
2. Rotate every credential that service could reach (its own token, API keys it holds, the Infisical secrets it
   mounts), then force-recreate the services that use them ([operations](operations.md#rotate-a-secret)).
3. Update the service before reopening anything.
4. Look for persistence on the host: new users, `authorized_keys`, crontabs, systemd units, unknown containers,
   executables in `/tmp` and `/dev/shm`, unexpected outbound connections.
