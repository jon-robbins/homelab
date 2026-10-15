#!/bin/sh
# qBittorrent on-completion fan-out. Arg1 = infohash (%I).
# After deploy, set qBT prefs: autorun_enabled=true, autorun_program=/scripts/dispatcher.sh "%I"
HASH="$1"
[ -n "$HASH" ] || exit 0

EVENTS_URL="${QBT_DISPATCH_EVENTS_URL:-http://homelab-qbt-events:8101/v1/torrent-finished}"
TIMEOUT_SECS="${QBT_DISPATCH_TIMEOUT_SECS:-5}"

post_one() {
  name="$1"
  url="$2"
  body="$3"
  resp_file=$(mktemp)
  hdr_file=$(mktemp)
  wget -S -O "$resp_file" --timeout="$TIMEOUT_SECS" \
    --header="Content-Type: application/json" \
    --post-data="$body" \
    "$url" 2>"$hdr_file" || true
  # Match status lines only (e.g. "HTTP/1.0 200 OK"), not "Server: BaseHTTP/0.6".
  code=$(grep -E 'HTTP/[0-9.]+ [0-9]{3}' "$hdr_file" 2>/dev/null | tail -1 | awk '{print $2}')
  [ -n "$code" ] || code=000

  handled="?"
  action="?"
  reason="?"
  ok="?"
  if [ -s "$resp_file" ]; then
    handled=$(sed -n 's/.*"handled"[[:space:]]*:[[:space:]]*\(true\|false\).*/\1/p' "$resp_file" | head -1)
    ok=$(sed -n 's/.*"ok"[[:space:]]*:[[:space:]]*\(true\|false\).*/\1/p' "$resp_file" | head -1)
    action=$(sed -n 's/.*"action"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$resp_file" | head -1)
    reason=$(sed -n 's/.*"reason"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$resp_file" | head -1)
    [ -n "$handled" ] || handled="$ok"
  fi

  soft_block=0
  # Disk optimization is best-effort: never fail the completion bus.
  if [ "$name" = "disk-optimize" ]; then
    soft_block=1
  fi

  echo "qbt-dispatch: service=$name http=$code handled=${handled:-?} action=${action:-?} reason=${reason:-?} hash=$HASH"

  rm -f "$resp_file" "$hdr_file"

  if [ "$soft_block" -eq 1 ]; then
    return 0
  fi
  [ "$code" = "200" ] && return 0
  return 1
}

BODY="{\"hash\":\"$HASH\"}"
FAIL=0
post_one events "$EVENTS_URL" "$BODY" &
pid=$!
wait $pid || FAIL=1
exit $FAIL
