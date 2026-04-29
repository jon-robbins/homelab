# Prowlarr Caddyfile Routing & Indexer Sync

## 1. Problem Summary

When Prowlarr is hosted behind Caddy under a subpath (`/prowlarr`), using the wrong Caddyfile directive (`handle_path` instead of `handle`) breaks indexer sync to Sonarr, Radarr, and Readarr. The symptom is that the \*arr apps report **zero active indexers**, even though Prowlarr itself works fine for manual searches.

All routing is defined in a single hand-written Caddyfile at `caddy/Caddyfile` — there are no Docker labels involved.

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
- If the Caddyfile uses `handle_path /prowlarr*` (path-stripping), the `/prowlarr` prefix is **removed** before forwarding to Prowlarr's backend.
- Prowlarr's backend **expects** the `/prowlarr` prefix (because UrlBase is set), so it doesn't recognize the stripped path.
- Prowlarr returns an HTML error page instead of JSON.
- Sonarr/Radarr fail to parse the response:
  ```
  'doctype' is an unexpected token. The expected token is 'DOCTYPE'
  ```
- The indexer test fails, so Prowlarr marks the sync as broken, leaving the \*arr apps with **zero indexers**.

## 4. The Fix

In `caddy/Caddyfile`, use `handle` (path-preserving) instead of `handle_path` (path-stripping):

```diff
- handle_path /prowlarr* {
-     reverse_proxy prowlarr:9696
- }
+ handle /prowlarr* {
+     reverse_proxy prowlarr:9696
+ }
```

This keeps the `/prowlarr` prefix intact when forwarded to the backend — matching what Prowlarr expects with its UrlBase setting.

**Important:** All \*arr services with a UrlBase use the same pattern in the Caddyfile:

```caddyfile
# Arr stack — path-preserving (services have UrlBase set)
handle /sonarr* {
    reverse_proxy sonarr:8989
}
handle /radarr* {
    reverse_proxy radarr:7878
}
handle /prowlarr* {
    reverse_proxy prowlarr:9696
}
handle /readarr* {
    reverse_proxy readarr:8787
}
```

After changing, reload Caddy:

```bash
docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

## 5. The Rule

> **Rule of thumb:** When an \*arr service has a non-empty `UrlBase` (e.g., `/sonarr`, `/radarr`, `/prowlarr`), the Caddyfile **must** use `handle` (path-preserving), NOT `handle_path` (path-stripping). The two must always agree — if UrlBase expects the prefix, Caddy must preserve it.

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

- **Caddyfile location:** `caddy/Caddyfile` (source of truth, mounted into the Caddy container)
- **Prowlarr config:** `data/prowlarr/config.xml` (`<UrlBase>/prowlarr</UrlBase>`)
- **Sonarr config:** `data/sonarr/config.xml` (`<UrlBase>/sonarr</UrlBase>`)
- **Radarr config:** `data/radarr/config.xml` (`<UrlBase>/radarr</UrlBase>`)

---

*Last updated: April 2026*
