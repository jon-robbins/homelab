# Service troubleshooting

## qBittorrent missing files

If a completed torrent reports `missingFiles`, verify that its content path is
mounted in the qBittorrent container and that the host path is mounted. Use
the generic offload worker or qBittorrent's own location and recheck actions;
avoid moving data behind qBittorrent's state.

## qBittorrent exclusive sessions

The `qbt_exclusive_*` tools pause non-matching torrents for one controlled
download session. Inspect `data/qbt-events/` and worker logs when a session
does not release its paused set.

## Arr items that never download

Monitored does not mean an Arr item is searched continuously. Run the
`homelab-arr-missing-search` worker for a capped backfill, or use the Arr
Wanted/Missing search controls. Check its status file and container health
when a request remains queued.

VPN and host-level tunnel management are outside this project.
