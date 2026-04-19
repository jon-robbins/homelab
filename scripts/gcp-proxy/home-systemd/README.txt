Install on the Linux host that runs Prowlarr (same machine where localhost:9696 works).

1) Create the VM from repo root (needs gcloud + GCP_PROJECT):
     cd gcp && export GCP_PROJECT="your-project-id" && ./create-vm.sh

2) Copy unit file:
     sudo cp gcp/home-systemd/prowlarr-gcp-proxy-tunnel@.service /etc/systemd/system/

3) Config with VM IP:
     sudo cp gcp/home-systemd/etc-default.prowlarr-gcp-proxy.example /etc/default/prowlarr-gcp-proxy
     sudo nano /etc/default/prowlarr-gcp-proxy   # set GCP_PROXY_IP

4) Enable for your login user (SSH private key must be in /home/USER/.ssh/ for that user):
     sudo systemctl daemon-reload
     sudo systemctl enable --now prowlarr-gcp-proxy-tunnel@YOURUSER.service

5) Prowlarr → Settings → General → Proxy:
     - Enable proxy
     - Host: 127.0.0.1
     - Port: 8888
     - Username/password: empty
     - (If the UI asks for a URL: http://127.0.0.1:8888 )

6) Test indexer again. FlareSolverr can stay http://127.0.0.1:8191 on the host.

Optional: autossh for faster reconnects — install autossh and replace ExecStart with autossh -M 0 ...

Tighten GCP firewall: allow tcp:22 only from your home IP instead of 0.0.0.0/0.
