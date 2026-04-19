All-Docker layout
==================

1) One-time Google login (opens browser; stores creds under ~/.config/gcloud):
     docker run --rm -it -v "$HOME/.config/gcloud:/root/.config/gcloud" google/cloud-sdk:slim gcloud auth login

2) Create the VM (tinyproxy runs in Docker on the instance; SSH key is ~/.ssh/id_ed25519):
     export GCP_PROJECT="your-gcp-project-id"
     ./gcp/provision-gcp-vm.sh

3) Put the VM IP in the compose env file next to docker-compose.yml:
     echo "GCP_PROXY_IP=1.2.3.4" >> .env

4) Build and start only the tunnel (Prowlarr stack unchanged):
     docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d gcp-proxy-tunnel

5) Prowlarr UI → Settings → General → Proxy: host 127.0.0.1, port 8888.
   If you deployed BasicAuth tinyproxy (recommended), set the same username/password here.

Proxy BasicAuth (recommended)
=============================
The SSH tunnel only proves you reached your VM; HTTP BasicAuth on tinyproxy means the
proxy itself is not usable without credentials (e.g. anything else on the homelab that
could reach 127.0.0.1:8888).

1) Choose a long random password (12+ chars; only [A-Za-z0-9._~+-] for user and password).

2) In .env next to docker-compose.yml:

     GCP_PROXY_IP=YOUR_VM_EXTERNAL_IP
     TINYPROXY_USER=prowlarr
     TINYPROXY_PASSWORD=your-secret-here

3) Deploy the auth-enabled image to the VM:

     cd /path/to/arr && set -a && source .env && set +a && ./gcp/deploy-tinyproxy-auth.sh

4) Prowlarr → Settings → General → Proxy: same host/port plus Username and Password.

5) Restart the tunnel if needed: docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d gcp-proxy-tunnel

Optional: tighten GCP firewall rule *-ssh to your home IP. Optional: autossh-style behaviour by leaving restart: unless-stopped on the tunnel container.

Existing VM (e.g. flaresolverr-vpn) — SSH key rejected / tunnel crash-loop
============================================================================
The tunnel uses SSH with ~/.ssh/id_ed25519. If the VM was created outside
provision-gcp-vm.sh, add your homelab public key to instance metadata:

  export GCP_PROJECT="homelab-474311"
  export GCP_ZONE="us-central1-f"
  ./gcp/authorize-ssh-key-on-vm.sh

Then verify tinyproxy is listening on the VM (8888 on loopback):

  ssh -i ~/.ssh/id_ed25519 ubuntu@YOUR_EXTERNAL_IP 'sudo ss -tlnp | grep 8888'

If nothing listens, run the same Docker tinyproxy block as in startup-tinyproxy.sh
on the instance, or recreate the VM with provision-gcp-vm.sh.

Restart the tunnel:

  docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d gcp-proxy-tunnel

Legacy systemd tunnel (host ssh, no Docker): see gcp/home-systemd/README.txt
