# Architecture

[← Back to README](../README.md)

Full service diagram, repository layout, network summary, and data-flow view.
For WAN port ownership see [ingress.md](ingress.md).

<details>
<summary>▶ Full architecture (all services)</summary>

![Homelab architecture](images/readme-architecture.svg)

<details>

<summary>diagram source</summary>

```mermaid
flowchart LR
    %% Styling Classes
    classDef external fill:#f8f9fa,stroke:#ced4da,stroke-width:2px;
    classDef ingress fill:#d1e7dd,stroke:#0f5132,stroke-width:2px;
    classDef core fill:#cfe2ff,stroke:#084298,stroke-width:2px;
    classDef media fill:#e2e3e5,stroke:#41464b,stroke-width:2px;
    classDef storage fill:#fff3cd,stroke:#856404,stroke-width:2px;

    %% External / Entry Points
    User([User Devices]):::external
    Tailscale(Tailscale VPN):::ingress
    Cloudflare(Cloudflare Tunnel):::ingress

    subgraph Front ["Ingress & Core Routing"]
        Caddy{Caddy Proxy}:::ingress
    end

    subgraph Services ["Service Mesh (homelab_net)"]
        direction TB
        subgraph MediaStack ["Media Management"]
            Overseerr(Overseerr):::media
            Sonarr(Sonarr):::media
            Radarr(Radarr):::media
            Readarr(Readarr):::media
            Qbit(qBittorrent):::media
            Prowlarr(Prowlarr):::media
            FlareSolverr(FlareSolverr):::media
            Autobrr(autobrr):::media
            Bazarr(Bazarr):::media
            RatioSched(homelab-ratio-scheduler):::media
            MissingSearch(homelab-arr-missing-search):::media
        end

        subgraph AI_Tools ["AI & Tooling"]
            Ollama(Ollama):::core
            MediaAgent(Media Agent):::core
            HomelabMcp("homelab-mcp\n(diagnostic tooling)"):::core
        end

        subgraph Files ["File sharing"]
            FileBrowser(FileBrowser Quantum):::core
        end
    end

    subgraph HostServices ["Host Networking / Casting"]
        Plex(Plex):::media
        Jellyfin(Jellyfin):::media
    end

    subgraph Storage ["Persistent Storage"]
        Data[(./data/* State)]:::storage
        MediaHDD[(Media HDD)]:::storage
        MediaNVMe[(Media NVMe)]:::storage
        PlexData[(Plex Data)]:::storage
    end

    %% Network Routing
    User -->|HTTPS| Caddy
    User -.->|Private IP| Tailscale
    Cloudflare -->|Tunnel| Caddy

    %% Proxy to Services
    Caddy --> Overseerr & Sonarr & Qbit & FileBrowser

    %% Backend Communication
    Sonarr & Radarr & Readarr --> Prowlarr
    Prowlarr --> FlareSolverr
    MediaAgent --> Sonarr & Radarr & Ollama
    Autobrr --> Qbit
    RatioSched -.->|Manage freeleech batching| Qbit

    %% Storage Mapping (Kept clean)
    Plex & Jellyfin ==> MediaHDD & MediaNVMe
    Plex ==> PlexData
    Services -.->|Runtime State| Data
    FileBrowser -.->|Browse| MediaHDD & MediaNVMe
```

</details>

</details>

## Repository Structure

```text
.
├── docker-compose.yml                # Root bundle (`include` → compose/base.yml)
├── compose/
│   ├── base.yml                      # Aggregates compose fragments + homelab_net / llm_net
│   ├── network.yml                   # Edge services (Caddy, Tailscale, cloudflared)
│   ├── arr.yml                       # *arr stack (Sonarr, Radarr, Prowlarr, Bazarr, Overseerr, FlareSolverr)
│   ├── media.yml                     # Playback (Plex, Jellyfin)
│   ├── download.yml                  # qBittorrent + download-path mounts
│   ├── workers.yml                   # homelab-workers services (offload and maintenance)
│   ├── files.yml                     # FileBrowser Quantum (share.*)
│   ├── autobrr.yml                   # Autobrr release automation
│   └── llm.yml                       # Ollama, internal dashboard, homelab-mcp
├── .env.example                      # Baseline environment contract
├── config/
│   ├── caddy/                        # Caddyfile + Dockerfile for local image
│   ├── cloudflared/                  # Tunnel template + README
│   ├── filebrowser/                  # FileBrowser Quantum config
├── apps/
│   ├── homelab-workers/              # Python worker package (pyproject + src layout)
│   ├── homelab-mcp/                  # MCP diagnostic/agent tooling
│   └── media-agent/                  # Media agent API service
├── scripts/
│   ├── ops/setup.sh
│   ├── README.md
│   └── hardening/                    # Host hardening scripts and security audit notes
├── tests/                            # compose / runtime / explicit integration smoke tests
└── data/                             # Runtime state (gitignored)
```

## Network Architecture

Bridge networking is the default because it limits exposure and keeps service-to-service DNS stable. Host mode is used only where protocol behavior requires it. WAN/LAN port ownership (`:443` VPN, `:8443` media, tunnel paths) is documented in [`ingress.md`](ingress.md).

| Service | Network mode | Why this mode is used | Security trade-off |
|---|---|---|---|
| `tailscale` | `host` | Needs `/dev/net/tun` and low-level networking | Elevated capabilities (`NET_ADMIN`, `NET_RAW`) |
| `cloudflared` | `host` | Tunnel agent sits on host ingress/egress boundary | Treat as edge component; protect tokens |
| `plex` | `host` | Improves LAN discovery and client compatibility | Media service directly reachable on host |
| `jellyfin` | `host` | Improves LAN discovery and client compatibility | Media service directly reachable on host |
| Most others (`caddy`, Arr apps, FileBrowser, LLM services, media-agent) | `bridge` on `homelab_net` | Internal-only mesh with Caddy ingress | Smaller attack surface and centralized routing |

## Data Flow Diagram

![Media data flow](images/readme-data-flow.svg)

<details>
<summary>diagram source</summary>

```mermaid
flowchart LR
    %% -- Styling --
    classDef user fill:#f8f9fa,stroke:#ced4da,stroke-width:2px;
    classDef request fill:#cfe2ff,stroke:#084298,stroke-width:2px;
    classDef arr fill:#e2e3e5,stroke:#41464b,stroke-width:2px;
    classDef download fill:#fff3cd,stroke:#856404,stroke-width:2px;
    classDef player fill:#d1e7dd,stroke:#0f5132,stroke-width:2px;
    %% -- User Entry --
    User([User]):::user

    %% -- Lifecycle Stages --
    subgraph Phase1 ["1. Discovery"]
        Overseerr(Overseerr):::request
    end

    subgraph Phase2 ["2. Management"]
        Arr["Sonarr / Radarr<br/>TV and movies"]:::arr
    end

    subgraph Phase3 ["3. Acquisition"]
        Prowlarr(Prowlarr):::download
        Qbit(qBittorrent):::download
    end

    subgraph Phase4 ["4. Subtitles"]
        Bazarr(Bazarr):::arr
    end

    subgraph Phase5 ["5. Playback"]
        Players["Plex / Jellyfin"]:::player
    end

    %% -- Main Request Flow --
    User -->|Requests Media| Overseerr
    Overseerr -->|Approves| Arr

    %% -- Search & Download --
    Arr -->|Search Indexers| Prowlarr
    Prowlarr -->|Sends Release| Qbit

    %% -- Feedback Loop (Dotted to reduce visual weight) --
    Qbit -.->|Import Complete| Arr
    Arr -.->|Sync Library| Bazarr
    Bazarr -.->|Downloads Subtitles| Players

    %% -- Final Media Refresh (Heavy lines for state change) --
    Arr ==>|Webhook: Update Library| Players
```

</details>
