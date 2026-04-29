# Prowlarr Caddy Routing & Indexer Sync

## 1. Problem Summary

When Prowlarr is hosted behind Caddy under a subpath (`/prowlarr`), using the wrong Caddy directive (`handle_path` instead of `handle`) breaks indexer sync to Sonarr, Radarr, and Readarr. The symptom is that the \*arr apps report **zero active indexers**, even though Prowlarr itself works fine for manual searches.

## 2. Symptom Chain

The full failure chain looks like this:

1. User requests a show/movie via Overseerr.
2. Overseerr sends the request to Sonarr/Radarr.
3. Sonarr/Radarr tries to search indexers but logs:
   ```
   Searching indexers for [Title]. 0 active indexers
   ```
4. No results found — the request sits in "Requested" state indefinitely.
5. Meanwhile, searching the same title **directly in Prowlarr's UI** works fine and returns results.

This is confusing because Prowlarr appears healthy — the problem is only visible from the \*arr side.

## 3. Root Cause

- Prowlarr is configured with `UrlBase: /prowlarr` in `data/prowlarr/config.xml`.
- When Prowlarr syncs indexers to Sonarr/Radarr, it provides indexer proxy URLs like:
  ```
  http://prowlarr:9696/prowlarr/api/v3/indexer/{id}/proxy/...
  ```
- If Caddy uses `handle_path /prowlarr*` (path-stripping), the `/prowlarr` prefix is **removed** before forwarding to Prowlarr's backend.
- Prowlarr's backend **expects** the `/prowlarr` prefix (because UrlBase is set), so it doesn't recognize the stripped path.
- Prowlarr returns an HTML error page instead of JSON.
- Sonarr/Radarr fail to parse the response:
  ```
  'doctype' is an unexpected token. The expected token is 'DOCTYPE'
  ```
- The indexer test fails, so Prowlarr marks the sync as broken, leaving the \*arr apps with **zero indexers**.

## 4. The Fix

**In the Caddyfile** (`data/caddy/Caddyfile`):

```diff
- handle_path /prowlarr* {
+ handle /prowlarr* {
      reverse_proxy prowlarr:9696
  }
```

This changes from path-stripping to path-preserving, so the `/prowlarr` prefix stays intact when forwarded to the backend — matching what Prowlarr expects with its UrlBase setting.

**Important:** Sonarr and Radarr already use `handle` (path-preserving) for the same reason — they also have UrlBase set (`/sonarr`, `/radarr`). All \*arr services behind Caddy subpaths should use `handle`, not `handle_path`.

After changing, reload Caddy:

```bash
docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

## 5. The Rule

> **Rule of thumb:** When an \*arr service has a non-empty `UrlBase` (e.g., `/sonarr`, `/radarr`, `/prowlarr`), Caddy **must** use `handle` (path-preserving), NOT `handle_path` (path-stripping). The two must always agree — if UrlBase expects the prefix, Caddy must preserve it.

## 6. Verification

Confirm the fix worked by checking that Sonarr sees active indexers:

```bash
# Check Sonarr has active indexers
SONARR_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' sonarr)
SONARR_KEY=$(grep -oP '(?<=<ApiKey>)[^<]+' data/sonarr/config.xml)
curl -s "http://${SONARR_IP}:8989/sonarr/api/v3/indexer" \
  -H "X-Api-Key: ${SONARR_KEY}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{len(d)} active indexers')"
```

If it still shows 0, trigger a re-sync from Prowlarr's **Settings → Apps → (each app) → Test/Sync**.

## 7. Related Configuration

The docker-compose labels in `compose/docker-compose.media.yml` use `caddy.handle` (correct), not `caddy.handle_path`. The Caddyfile at `data/caddy/Caddyfile` is the runtime config that Caddy actually uses — make sure both stay in sync.

---

*Last updated: April 2026*
