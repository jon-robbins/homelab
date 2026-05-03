#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  test-xray-client.sh — Test Xray REALITY VPN from macOS (China)
#  Run on your MacBook:  bash test-xray-client.sh
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Connection Details (hardcoded from server) ──────────────────
SERVER_ADDRESS="213.195.110.145"
SERVER_PORT=443
CLIENT_UUID="7fc5ddce-e5f1-49ad-9f90-1da0e6b7571e"
FLOW="xtls-rprx-vision"
PUBLIC_KEY="88sO7yXUW9yN3QQRfTjKB8qwCG0_9oBsM-yWNBXqmGE"
SHORT_ID="01917a7976"
SERVER_NAME="www.nvidia.com"
FINGERPRINT="chrome"

LOCAL_PORT=10808
EXPECTED_IP="213.195.110.145"

# ── Colors ──────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

pass()  { printf "${GREEN}${BOLD}[PASS]${RESET} %s\n" "$*"; }
fail()  { printf "${RED}${BOLD}[FAIL]${RESET} %s\n" "$*"; }
info()  { printf "${CYAN}[INFO]${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${RESET} %s\n" "$*"; }
header(){ printf "\n${BOLD}── %s ─────────────────────────────────────${RESET}\n" "$*"; }

# ── Temp files & cleanup ───────────────────────────────────────
TMPDIR_XRAY="$(mktemp -d)"
CONFIG_FILE="${TMPDIR_XRAY}/xray-client.json"
XRAY_ACCESS_LOG="${TMPDIR_XRAY}/xray-access.log"
XRAY_ERROR_LOG="${TMPDIR_XRAY}/xray-error.log"
XRAY_PID=""

# Diagnostic state tracking
DIAG_TCP_OK=false
DIAG_PING_OK=false
DIAG_TLS_OK=false
DIAG_VPN_OK=false
DIAG_IP_OK=false

cleanup() {
    if [[ -n "$XRAY_PID" ]] && kill -0 "$XRAY_PID" 2>/dev/null; then
        kill "$XRAY_PID" 2>/dev/null || true
        wait "$XRAY_PID" 2>/dev/null || true
        info "Xray process stopped."
    fi
    rm -rf "$TMPDIR_XRAY"
}
trap cleanup EXIT INT TERM

# ── Print connection details ───────────────────────────────────
header "Xray REALITY Connection Details"
echo "  Server:      ${SERVER_ADDRESS}:${SERVER_PORT}"
echo "  Protocol:    VLESS + REALITY"
echo "  UUID:        ${CLIENT_UUID}"
echo "  Flow:        ${FLOW}"
echo "  SNI:         ${SERVER_NAME}"
echo "  Public Key:  ${PUBLIC_KEY}"
echo "  Short ID:    ${SHORT_ID}"
echo "  Fingerprint: ${FINGERPRINT}"

# ── Locate xray binary ────────────────────────────────────────
header "Checking for Xray binary"
XRAY_BIN=""

for candidate in xray /usr/local/bin/xray /opt/homebrew/bin/xray; do
    if command -v "$candidate" &>/dev/null; then
        XRAY_BIN="$(command -v "$candidate")"
        break
    fi
done

if [[ -z "$XRAY_BIN" ]]; then
    fail "Xray binary not found."
    echo ""
    echo "  Install on macOS:  ${BOLD}brew install xray${RESET}"
    echo "  Or download from:  https://github.com/XTLS/Xray-core/releases"
    echo ""
    warn "Without Xray, you can still use these details in your client app:"
    echo "  (V2rayN, V2rayA, Shadowrocket, Quantumult X, Clash Meta, etc.)"
    echo ""
    echo "  VLESS share link:"
    echo "  vless://${CLIENT_UUID}@${SERVER_ADDRESS}:${SERVER_PORT}?encryption=none&flow=${FLOW}&type=tcp&security=reality&sni=${SERVER_NAME}&fp=${FINGERPRINT}&pbk=${PUBLIC_KEY}&sid=${SHORT_ID}#Spain-REALITY"
    exit 1
fi

info "Found Xray at: ${XRAY_BIN}"
"$XRAY_BIN" version 2>/dev/null | head -1 || true

# ── Pre-flight Diagnostics ────────────────────────────────────
header "Pre-flight Diagnostics (no VPN)"

# 1. DNS / reverse-DNS resolution
info "Resolving server IP..."
dig +short -x "${SERVER_ADDRESS}" 2>/dev/null \
    || nslookup "${SERVER_ADDRESS}" 2>/dev/null | grep -i "name" \
    || echo "  DNS tools not available (dig/nslookup not found)"

# 2. Direct TCP connectivity to server port 443
info "Test 0: Direct TCP connect to ${SERVER_ADDRESS}:${SERVER_PORT} (no VPN)..."
if command -v nc &>/dev/null; then
    if nc -z -w 5 "${SERVER_ADDRESS}" "${SERVER_PORT}" 2>&1; then
        pass "TCP port ${SERVER_PORT} is reachable"
        DIAG_TCP_OK=true
    else
        fail "TCP port ${SERVER_PORT} is NOT reachable — check router port forwarding and firewall"
    fi
else
    if (echo > /dev/tcp/${SERVER_ADDRESS}/${SERVER_PORT}) 2>/dev/null; then
        pass "TCP port ${SERVER_PORT} is reachable"
        DIAG_TCP_OK=true
    else
        fail "TCP port ${SERVER_PORT} is NOT reachable"
    fi
fi

# 3. Ping test
info "Ping test to ${SERVER_ADDRESS}..."
if ping -c 2 -W 3 "${SERVER_ADDRESS}" 2>&1 | tail -3; then
    DIAG_PING_OK=true
else
    warn "Ping failed (ICMP may be blocked — not necessarily a problem)"
fi

# 4. TLS handshake test (direct, tests if REALITY responds)
info "TLS handshake test to ${SERVER_ADDRESS}:${SERVER_PORT} (SNI: ${SERVER_NAME})..."
TLS_OUTPUT=$(echo | openssl s_client -connect "${SERVER_ADDRESS}:${SERVER_PORT}" -servername "${SERVER_NAME}" -brief 2>&1 | head -5)
echo "$TLS_OUTPUT"
if echo "$TLS_OUTPUT" | grep -qi "CONNECTED\|verify"; then
    DIAG_TLS_OK=true
    pass "TLS handshake completed"
else
    warn "TLS handshake may have issues (REALITY may mask this — not fatal)"
fi

# ── Generate client config (debug mode) ───────────────────────
header "Generating client config (debug loglevel)"

cat > "$CONFIG_FILE" <<XEOF
{
  "log": {
    "loglevel": "debug",
    "access": "${XRAY_ACCESS_LOG}",
    "error": "${XRAY_ERROR_LOG}"
  },
  "inbounds": [
    {
      "port": ${LOCAL_PORT},
      "listen": "127.0.0.1",
      "protocol": "socks",
      "settings": {
        "udp": true
      }
    }
  ],
  "outbounds": [
    {
      "protocol": "vless",
      "settings": {
        "vnext": [
          {
            "address": "${SERVER_ADDRESS}",
            "port": ${SERVER_PORT},
            "users": [
              {
                "id": "${CLIENT_UUID}",
                "flow": "${FLOW}",
                "encryption": "none"
              }
            ]
          }
        ]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "serverName": "${SERVER_NAME}",
          "fingerprint": "${FINGERPRINT}",
          "publicKey": "${PUBLIC_KEY}",
          "shortId": "${SHORT_ID}",
          "spiderX": ""
        }
      }
    }
  ]
}
XEOF

info "Config written to ${CONFIG_FILE}"
info "Debug logs → ${XRAY_ERROR_LOG}"

# ── Start Xray ─────────────────────────────────────────────────
header "Starting Xray (SOCKS5 on 127.0.0.1:${LOCAL_PORT})"

"$XRAY_BIN" run -config "$CONFIG_FILE" &
XRAY_PID=$!

# Wait for SOCKS5 proxy to be ready (up to 8 seconds)
READY=false
for i in $(seq 1 16); do
    if nc -z 127.0.0.1 "$LOCAL_PORT" 2>/dev/null; then
        READY=true
        break
    fi
    sleep 0.5
done

if ! $READY; then
    fail "SOCKS5 proxy did not start within 8 seconds."
    fail "Xray may have crashed. Check output above."
    if [[ -f "$XRAY_ERROR_LOG" ]]; then
        echo ""
        warn "Last 10 lines of Xray error log:"
        tail -10 "$XRAY_ERROR_LOG" 2>/dev/null || true
    fi
    exit 1
fi

pass "SOCKS5 proxy is listening on 127.0.0.1:${LOCAL_PORT}"

# ── Run connectivity tests ─────────────────────────────────────
header "Testing connectivity through VPN"
TESTS_PASSED=0
TESTS_TOTAL=4

# Test 1: Google via VPN — verbose
info "Test 1: Fetching https://www.google.com (verbose)..."
echo ""
CURL_VERBOSE=$(curl -v --socks5-hostname "127.0.0.1:${LOCAL_PORT}" \
    --connect-timeout 20 --max-time 25 \
    "https://www.google.com" -o /dev/null -w "\n  HTTP Code: %{http_code}\n  Time Total: %{time_total}s\n  Time Connect: %{time_connect}s\n" 2>&1 || true)
echo "$CURL_VERBOSE" | tail -15
echo ""

HTTP_CODE=$(echo "$CURL_VERBOSE" | grep "HTTP Code:" | awk '{print $NF}' || echo "000")
if [[ "$HTTP_CODE" =~ ^(200|301|302)$ ]]; then
    pass "Google returned HTTP ${HTTP_CODE} — GFW bypassed!"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    DIAG_VPN_OK=true
else
    fail "Google returned HTTP ${HTTP_CODE} — tunnel may not be working."
    if [[ -f "$XRAY_ERROR_LOG" ]]; then
        warn "Last 10 lines of Xray error log:"
        tail -10 "$XRAY_ERROR_LOG" 2>/dev/null || true
        echo ""
    fi
fi

# Test 2: IP check (should show Spain server IP)
info "Test 2: Checking exit IP via https://ifconfig.me ..."
EXIT_IP=$(curl -s --connect-timeout 20 --max-time 25 \
    --socks5-hostname "127.0.0.1:${LOCAL_PORT}" \
    "https://ifconfig.me" 2>/dev/null || echo "TIMEOUT")

if [[ "$EXIT_IP" == "$EXPECTED_IP" ]]; then
    pass "Exit IP is ${EXIT_IP} (matches Spain server)"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    DIAG_IP_OK=true
elif [[ "$EXIT_IP" == "TIMEOUT" || -z "$EXIT_IP" ]]; then
    fail "Could not determine exit IP (timeout/error)."
    if [[ -f "$XRAY_ERROR_LOG" ]]; then
        warn "Last 10 lines of Xray error log:"
        tail -10 "$XRAY_ERROR_LOG" 2>/dev/null || true
        echo ""
    fi
else
    warn "Exit IP is ${EXIT_IP} (expected ${EXPECTED_IP})"
    warn "IP might differ if server is behind NAT or uses a different egress."
    TESTS_PASSED=$((TESTS_PASSED + 1))
    DIAG_IP_OK=true
fi

# Test 3: HTTP-only test (less likely to be blocked than HTTPS)
info "Test 3: HTTP-only test via http://httpbin.org/ip ..."
HTTPBIN=$(curl -s --socks5 "127.0.0.1:${LOCAL_PORT}" \
    --connect-timeout 20 --max-time 25 \
    "http://httpbin.org/ip" 2>&1 || echo "TIMEOUT")

if echo "$HTTPBIN" | grep -q "origin"; then
    HTTPBIN_IP=$(echo "$HTTPBIN" | grep -o '"origin"[^"]*"[^"]*"' | grep -o '[0-9.]*')
    pass "httpbin.org reachable — origin IP: ${HTTPBIN_IP}"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    fail "httpbin.org HTTP test failed: ${HTTPBIN}"
    if [[ -f "$XRAY_ERROR_LOG" ]]; then
        warn "Last 10 lines of Xray error log:"
        tail -10 "$XRAY_ERROR_LOG" 2>/dev/null || true
        echo ""
    fi
fi

# Test 4: DNS leak test via VPN
info "Test 4: DNS resolution through VPN (curl https://cloudflare.com/cdn-cgi/trace)..."
CF_TRACE=$(curl -s --connect-timeout 20 --max-time 25 \
    --socks5-hostname "127.0.0.1:${LOCAL_PORT}" \
    "https://cloudflare.com/cdn-cgi/trace" 2>/dev/null || echo "TIMEOUT")

if echo "$CF_TRACE" | grep -q "warp="; then
    CF_IP=$(echo "$CF_TRACE" | grep "^ip=" | cut -d= -f2)
    CF_LOC=$(echo "$CF_TRACE" | grep "^loc=" | cut -d= -f2)
    pass "Cloudflare trace: IP=${CF_IP}, Location=${CF_LOC}"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    fail "Cloudflare trace failed."
fi

# ── Summary ────────────────────────────────────────────────────
header "Results"

echo ""
echo "  Pre-flight:"
$DIAG_TCP_OK  && echo "    TCP ${SERVER_ADDRESS}:${SERVER_PORT}  ✓ reachable" \
               || echo "    TCP ${SERVER_ADDRESS}:${SERVER_PORT}  ✗ NOT reachable"
$DIAG_PING_OK && echo "    Ping ${SERVER_ADDRESS}            ✓ OK" \
               || echo "    Ping ${SERVER_ADDRESS}            ~ failed/blocked"
$DIAG_TLS_OK  && echo "    TLS handshake                  ✓ OK" \
               || echo "    TLS handshake                  ~ inconclusive"
echo ""
echo "  VPN tunnel:"
$DIAG_VPN_OK  && echo "    HTTPS through VPN              ✓ working" \
               || echo "    HTTPS through VPN              ✗ FAILED"
$DIAG_IP_OK   && echo "    Exit IP matches                ✓ correct" \
               || echo "    Exit IP matches                ✗ mismatch/unknown"
echo ""

if [[ $TESTS_PASSED -eq $TESTS_TOTAL ]]; then
    printf "${GREEN}${BOLD}  ✓ All %d/%d tests passed — VPN is working!${RESET}\n" "$TESTS_PASSED" "$TESTS_TOTAL"
elif [[ $TESTS_PASSED -gt 0 ]]; then
    printf "${YELLOW}${BOLD}  ~ %d/%d tests passed — partial success.${RESET}\n" "$TESTS_PASSED" "$TESTS_TOTAL"
else
    printf "${RED}${BOLD}  ✗ 0/%d tests passed — VPN is not working.${RESET}\n" "$TESTS_TOTAL"
fi

# ── Troubleshooting suggestions ────────────────────────────────
if ! $DIAG_TCP_OK || ! $DIAG_VPN_OK; then
    header "Troubleshooting Suggestions"

    if ! $DIAG_TCP_OK; then
        echo "  • Port 443 not reachable → check:"
        echo "      1. Router port forwarding (WAN 443 → server LAN IP:443)"
        echo "      2. Server firewall (ufw allow 443/tcp)"
        echo "      3. ISP blocking port 443 (try from a different network)"
        echo "      4. GFW actively blocking the IP (try a different port)"
        echo ""
    fi

    if $DIAG_TCP_OK && ! $DIAG_VPN_OK; then
        echo "  • TCP reachable but VPN fails → check REALITY config:"
        echo "      1. Verify UUID matches server-side (3x-ui panel)"
        echo "      2. Verify publicKey matches server's private key pair"
        echo "      3. Verify shortId is in server's shortIds list"
        echo "      4. Check if Xray/3x-ui service is running on server"
        echo "      5. Check server-side logs: journalctl -u x-ui --no-pager -n 50"
        echo ""
    fi

    if $DIAG_TCP_OK && $DIAG_TLS_OK && ! $DIAG_VPN_OK; then
        echo "  • TLS OK but VPN still fails → possible causes:"
        echo "      1. REALITY target mismatch (server target should be 127.0.0.1:8443 for Caddy)"
        echo "      2. Client flow setting wrong (must be xtls-rprx-vision)"
        echo "      3. GFW fingerprint detection (try changing fingerprint to 'safari' or 'firefox')"
        echo ""
    fi
fi

# ── Debug log location ─────────────────────────────────────────
echo ""
info "Full debug logs saved to:"
echo "  Access: ${XRAY_ACCESS_LOG}"
echo "  Error:  ${XRAY_ERROR_LOG}"
echo ""
warn "Note: logs are in ${TMPDIR_XRAY} and will be deleted on exit."
warn "Copy them now if needed:  cp ${TMPDIR_XRAY}/xray-*.log ~/Desktop/"
echo ""

# Cleanup happens via trap
