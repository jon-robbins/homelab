# Hardware & Storage

[← Back to README](../README.md)

Physical server specs, storage tiers, and GPU assumptions for this homelab stack.

### Services

| Media | Notes |
|-------|--------|
| **Arr stack** | Seerr (Overseerr), Sonarr, Radarr, Readarr, Prowlarr, Bazarr, autobrr, qBittorrent |
| **Playback** | Plex + Jellyfin — both; Jellyfin’s Apple TV client still isn’t mature enough to drop Plex |

| App | Notes |
|------------------|--------|
| **Pi-hole** | LAN DNS / blocking |
| **Cloudflare** | Tunneling (no open inbound ports for public names) |
| **Caddy** | HTTPS reverse proxy — subpath routing on one hostname (e.g. `/sonarr`, `/radarr`) + subdomain routing for Plex, Jellyfin, and Pi-hole |
| **Dashy** | Central dashboard |
| **Tailscale** | Remote mesh VPN |
| **Telegram bot** | Gemini Flash + Gemma 31b (4-bit quantized) for torrent search — yes, alongside Seerr; the GPU is there, so why not |

### Server

| Component | Spec |
|-----------|------|
| CPU | AMD Ryzen 9 7950X3D |
| RAM | DDR5 — 96GB 6200 MT/s |
| GPU | NVIDIA GeForce RTX 3090 |
| Board | ASUS PRIME X670E-PRO WIFI |
| OS | Ubuntu Server 24.04 LTS |

| Storage | Role |
|---------|------|
| 1 TB NVMe | EFI, boot, OS |
| 4 TB NVMe | Hot / frequently used media |
| ~16 TB HDD | Long-term library + backups |


![License](https://img.shields.io/badge/license-MIT-green)
![Docker Compose](https://img.shields.io/badge/docker%20compose-v2+-blue)

## GPU Acceleration

GPU support is enabled by default (see `gpus: all` on Plex/Jellyfin and Ollama). This expects NVIDIA Container Toolkit on the host.
