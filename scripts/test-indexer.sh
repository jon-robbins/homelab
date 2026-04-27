#!/usr/bin/env bash
set -uo pipefail
# ---------------------------------------------------------------------------
# test-indexer.sh – One-off Prowlarr indexer grab-capability diagnostic
# ---------------------------------------------------------------------------

# ── Load .env ──────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env not found at $ENV_FILE" >&2; exit 1
fi
set -a; source "$ENV_FILE"; set +a

: "${PROWLARR_API_KEY:?PROWLARR_API_KEY not set}"
: "${QBITTORRENT_USERNAME:?QBITTORRENT_USERNAME not set}"
: "${QBITTORRENT_PASSWORD:?QBITTORRENT_PASSWORD not set}"

PROWLARR_CTR="homelab-prowlarr-1"
QB_CTR="homelab-qbittorrent-1"
PROWLARR_BASE="http://localhost:9696"
API="X-Api-Key: ${PROWLARR_API_KEY}"
SEARCH_QUERY="matrix"

# Temp dir for intermediate JSON
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT

hr()  { printf '\n%s\n' "════════════════════════════════════════════════════════════════"; }
hdr() { hr; printf '  %s\n' "$1"; hr; }

# Helper: curl inside prowlarr container
pcurl() {
  docker exec "$PROWLARR_CTR" curl -sf --max-time 30 "$@"
}

# ── 1. List all indexers ───────────────────────────────────────────────────
hdr "1) Prowlarr Indexers"
pcurl -H "$API" "${PROWLARR_BASE}/api/v1/indexer" > "$TMPD/indexers.json" 2>/dev/null || {
  echo "FATAL: cannot reach Prowlarr API"; exit 1
}

python3 -c "
import json, sys
idxs = json.load(open('$TMPD/indexers.json'))
print(f'Found {len(idxs)} indexer(s)\n')
fmt = '{:<5} {:<35} {:<10} {:<10}'
print(fmt.format('ID','Name','Enabled','Protocol'))
print('-'*65)
for i in idxs:
    print(fmt.format(i['id'], i['name'][:34], str(i['enable']), i.get('protocol','?')))
"

# Collect indexer IDs
INDEXER_IDS=$(python3 -c "
import json
idxs = json.load(open('$TMPD/indexers.json'))
print(' '.join(str(i['id']) for i in idxs if i['enable']))
")

# ── 2. Check indexer health (test endpoint) ────────────────────────────────
hdr "2) Indexer Health (test endpoint)"

declare -A HEALTH_STATUS
for IDX_ID in $INDEXER_IDS; do
  IDX_JSON=$(python3 -c "
import json
idxs = json.load(open('$TMPD/indexers.json'))
for i in idxs:
    if i['id'] == $IDX_ID:
        print(json.dumps(i)); break
")
  IDX_NAME=$(python3 -c "import json; print(json.loads('$(echo "$IDX_JSON" | sed "s/'/\\\\'/g")')['name'])" 2>/dev/null || echo "id=$IDX_ID")

  # Write indexer json to file to avoid shell quoting issues
  echo "$IDX_JSON" > "$TMPD/idx_${IDX_ID}.json"
  docker cp "$TMPD/idx_${IDX_ID}.json" "$PROWLARR_CTR:/tmp/idx_${IDX_ID}.json" 2>/dev/null
  HTTP_CODE=$(docker exec "$PROWLARR_CTR" curl -s -o /tmp/test_resp.txt -w '%{http_code}' \
    --max-time 30 \
    -X POST -H "$API" -H "Content-Type: application/json" \
    -d @/tmp/idx_${IDX_ID}.json \
    "${PROWLARR_BASE}/api/v1/indexer/test" 2>/dev/null || echo "000")

  if [[ "$HTTP_CODE" == "200" ]]; then
    STATUS="OK"
  else
    STATUS="FAIL($HTTP_CODE)"
  fi
  HEALTH_STATUS[$IDX_ID]="$STATUS"
  printf "  %-35s  %s\n" "$IDX_NAME" "$STATUS"
done

# ── 3. Search via each indexer individually ────────────────────────────────
hdr "3) Per-Indexer Search: \"$SEARCH_QUERY\""

declare -A SEARCH_OK SEARCH_HAS_DL SEARCH_HAS_MAG SEARCH_COUNT

for IDX_ID in $INDEXER_IDS; do
  IDX_NAME=$(python3 -c "
import json
for i in json.load(open('$TMPD/indexers.json')):
    if i['id'] == $IDX_ID: print(i['name']); break
")
  ENCODED_Q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$SEARCH_QUERY'))")

  SEARCH_FILE="$TMPD/search_${IDX_ID}.json"
  HTTP_CODE=$(docker exec "$PROWLARR_CTR" curl -s -o /tmp/search_${IDX_ID}.json -w '%{http_code}' \
    --max-time 60 \
    -H "$API" \
    "${PROWLARR_BASE}/api/v1/search?query=${ENCODED_Q}&indexerIds=${IDX_ID}&type=search" 2>/dev/null || echo "000")

  # Copy result back to host
  docker cp "$PROWLARR_CTR:/tmp/search_${IDX_ID}.json" "$SEARCH_FILE" 2>/dev/null || echo '[]' > "$SEARCH_FILE"

  if [[ "$HTTP_CODE" =~ ^2 ]]; then
    SEARCH_OK[$IDX_ID]="Y"
    STATS=$(python3 -c "
import json, sys
try:
    results = json.load(open('$SEARCH_FILE'))
    if not isinstance(results, list): results = []
except: results = []
count = len(results)
has_dl = any(r.get('downloadUrl') for r in results)
has_mag = any(r.get('magnetUrl') for r in results)
print(f'{count}|{\"Y\" if has_dl else \"N\"}|{\"Y\" if has_mag else \"N\"}')
" 2>/dev/null || echo "0|N|N")
    IFS='|' read -r CNT DL MAG <<< "$STATS"
    SEARCH_COUNT[$IDX_ID]="$CNT"
    SEARCH_HAS_DL[$IDX_ID]="$DL"
    SEARCH_HAS_MAG[$IDX_ID]="$MAG"
  else
    SEARCH_OK[$IDX_ID]="N($HTTP_CODE)"
    SEARCH_COUNT[$IDX_ID]="0"
    SEARCH_HAS_DL[$IDX_ID]="N"
    SEARCH_HAS_MAG[$IDX_ID]="N"
  fi
  printf "  %-35s  results=%-4s  downloadUrl=%-3s  magnetUrl=%-3s  http=%s\n" \
    "$IDX_NAME" "${SEARCH_COUNT[$IDX_ID]}" "${SEARCH_HAS_DL[$IDX_ID]}" "${SEARCH_HAS_MAG[$IDX_ID]}" "$HTTP_CODE"
done

# ── 4. Test grab for each indexer ──────────────────────────────────────────
hdr "4) Grab Test (first result per indexer)"

declare -A GRAB_OK GRAB_ERR

for IDX_ID in $INDEXER_IDS; do
  IDX_NAME=$(python3 -c "
import json
for i in json.load(open('$TMPD/indexers.json')):
    if i['id'] == $IDX_ID: print(i['name']); break
")
  SEARCH_FILE="$TMPD/search_${IDX_ID}.json"

  # Need at least one result with a downloadUrl or guid
  GRAB_BODY=$(python3 -c "
import json, sys
try:
    results = json.load(open('$SEARCH_FILE'))
    if not isinstance(results, list) or len(results) == 0:
        sys.exit(1)
    # Pick first result that has a downloadUrl
    for r in results:
        if r.get('downloadUrl'):
            print(json.dumps(r))
            sys.exit(0)
    # Fallback: first result
    print(json.dumps(results[0]))
except:
    sys.exit(1)
" 2>/dev/null)

  if [[ -z "$GRAB_BODY" ]]; then
    GRAB_OK[$IDX_ID]="SKIP"
    GRAB_ERR[$IDX_ID]="no results to grab"
    printf "  %-35s  SKIP (no search results)\n" "$IDX_NAME"
    continue
  fi

  echo "$GRAB_BODY" > "$TMPD/grab_${IDX_ID}.json"
  docker cp "$TMPD/grab_${IDX_ID}.json" "$PROWLARR_CTR:/tmp/grab_${IDX_ID}.json" 2>/dev/null
  GRAB_HTTP=$(docker exec "$PROWLARR_CTR" curl -s -o /tmp/grab_resp_${IDX_ID}.txt -w '%{http_code}' \
    --max-time 30 \
    -X POST -H "$API" -H "Content-Type: application/json" \
    -d @/tmp/grab_${IDX_ID}.json \
    "${PROWLARR_BASE}/api/v1/search" 2>/dev/null || echo "000")

  docker cp "$PROWLARR_CTR:/tmp/grab_resp_${IDX_ID}.txt" "$TMPD/grab_resp_${IDX_ID}.txt" 2>/dev/null || true
  GRAB_BODY_RESP=$(cat "$TMPD/grab_resp_${IDX_ID}.txt" 2>/dev/null || echo "")

  if [[ "$GRAB_HTTP" =~ ^2 ]]; then
    GRAB_OK[$IDX_ID]="Y"
    GRAB_ERR[$IDX_ID]=""
    printf "  %-35s  OK (%s)\n" "$IDX_NAME" "$GRAB_HTTP"
  else
    GRAB_OK[$IDX_ID]="N"
    # Try to extract error message
    ERR_MSG=$(python3 -c "
import json, sys
try:
    d = json.loads(open('$TMPD/grab_resp_${IDX_ID}.txt').read())
    msg = d.get('message','') or d.get('errorMessage','') or str(d)[:120]
    print(msg[:120])
except:
    print('HTTP $GRAB_HTTP')
" 2>/dev/null || echo "HTTP $GRAB_HTTP")
    GRAB_ERR[$IDX_ID]="$ERR_MSG"
    printf "  %-35s  FAIL (%s) %s\n" "$IDX_NAME" "$GRAB_HTTP" "$ERR_MSG"
  fi
done

# ── 5. qBittorrent connectivity ───────────────────────────────────────────
hdr "5) qBittorrent Connectivity"

echo "  Checking qBittorrent container..."
if docker ps --format '{{.Names}}' | grep -q "$QB_CTR"; then
  echo "  Container $QB_CTR is running."
else
  echo "  WARNING: Container $QB_CTR is NOT running!"
fi

echo ""
echo "  Testing qBittorrent login from host..."
QB_LOGIN=$(curl -sf --max-time 10 -c - \
  -d "username=${QBITTORRENT_USERNAME}&password=${QBITTORRENT_PASSWORD}" \
  "http://127.0.0.1:8080/api/v2/auth/login" 2>/dev/null || echo "FAIL")
if echo "$QB_LOGIN" | grep -qi "ok\|SID"; then
  echo "  qBittorrent auth: OK"
else
  echo "  qBittorrent auth: FAIL — response: $QB_LOGIN"
fi

echo ""
echo "  Testing qBittorrent from Prowlarr container (network connectivity)..."
QB_NET=$(docker exec "$PROWLARR_CTR" curl -sf --max-time 10 \
  "http://qbittorrent:8080/api/v2/app/version" 2>/dev/null || echo "UNREACHABLE")
echo "  qBittorrent version from prowlarr network: $QB_NET"

# ── 6. Prowlarr download client config ────────────────────────────────────
hdr "6) Prowlarr Download Client Config"

pcurl -H "$API" "${PROWLARR_BASE}/api/v1/downloadclient" > "$TMPD/dlclients.json" 2>/dev/null || {
  echo "  Could not fetch download clients"; true
}

python3 -c "
import json
try:
    clients = json.load(open('$TMPD/dlclients.json'))
    if not clients:
        print('  No download clients configured!')
    for c in clients:
        print(f\"  [{c['id']}] {c['name']}  impl={c.get('implementation','')}  enable={c.get('enable','')}\")
        for f in c.get('fields',[]):
            if f.get('value') is not None and f['name'] in ('host','port','username','password','useSsl','category'):
                val = f['value']
                if f['name'] == 'password': val = '***'
                print(f\"       {f['name']}: {val}\")
except Exception as e:
    print(f'  Error: {e}')
" 2>/dev/null

# ── 7. Summary table ──────────────────────────────────────────────────────
hdr "7) SUMMARY"

python3 << 'PYEOF'
import json, sys, os

tmpd = os.environ.get("TMPD", "/tmp")

# This script is called with TMPD in env; read from shell vars via file
PYEOF

# Build summary with shell arrays
printf "  %-35s %-8s %-8s %-12s %-8s %s\n" \
  "INDEXER" "SEARCH" "DL_URL" "GRAB" "HEALTH" "ERROR"
printf "  %s\n" "$(printf '%.0s─' {1..100})"

for IDX_ID in $INDEXER_IDS; do
  IDX_NAME=$(python3 -c "
import json
for i in json.load(open('$TMPD/indexers.json')):
    if i['id'] == $IDX_ID: print(i['name']); break
")
  S_OK="${SEARCH_OK[$IDX_ID]:-?}"
  S_DL="${SEARCH_HAS_DL[$IDX_ID]:-?}"
  G_OK="${GRAB_OK[$IDX_ID]:-?}"
  H_ST="${HEALTH_STATUS[$IDX_ID]:-?}"
  G_ERR="${GRAB_ERR[$IDX_ID]:-}"

  printf "  %-35s %-8s %-8s %-12s %-8s %s\n" \
    "$IDX_NAME" "$S_OK" "$S_DL" "$G_OK" "$H_ST" "$G_ERR"
done

echo ""
echo "  Legend: SEARCH=search returned results, DL_URL=results have downloadUrl,"
echo "          GRAB=grab endpoint succeeded, HEALTH=indexer test passed"
echo ""

# Conclusion
echo "  ── Conclusion ──"
for IDX_ID in $INDEXER_IDS; do
  IDX_NAME=$(python3 -c "
import json
for i in json.load(open('$TMPD/indexers.json')):
    if i['id'] == $IDX_ID: print(i['name']); break
")
  G="${GRAB_OK[$IDX_ID]:-?}"
  if [[ "$G" == "Y" ]]; then
    echo "  ✓ $IDX_NAME — GRAB-CAPABLE"
  elif [[ "$G" == "SKIP" ]]; then
    echo "  ⊘ $IDX_NAME — NO RESULTS (cannot test grab)"
  else
    echo "  ✗ $IDX_NAME — SEARCH-ONLY (grab fails)"
  fi
done

echo ""
echo "Done."
