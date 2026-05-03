#!/bin/bash
set -e

# === Configuration ===
SERVER_IP="213.195.110.145"
SERVER_PORT=443
UUID="7fc5ddce-e5f1-49ad-9f90-1da0e6b7571e"
PUBLIC_KEY="88sO7yXUW9yN3QQRfTjKB8qwCG0_9oBsM-yWNBXqmGE"
SHORT_ID="01917a7976"
SNI="www.nvidia.com"
FLOW="xtls-rprx-vision"
LOCAL_SOCKS_PORT=10808

WORK_DIR=$(mktemp -d)
trap "echo ''; echo 'Cleaning up...'; kill %1 2>/dev/null; rm -rf $WORK_DIR" EXIT

echo "=== Xray VLESS-Reality Test ==="
echo "Work dir: $WORK_DIR"
echo ""

# === Step 1: Find Xray binary ===
echo "--- Step 1: Locating Xray ---"
XRAY_BIN=$(command -v xray 2>/dev/null || echo "")
if [ -z "$XRAY_BIN" ]; then
    # Try common Homebrew paths
    for p in /opt/homebrew/bin/xray /usr/local/bin/xray; do
        [ -x "$p" ] && XRAY_BIN="$p" && break
    done
fi
if [ -z "$XRAY_BIN" ]; then
    echo "ERROR: xray not found. Install with: brew install xray"
    exit 1
fi
echo "Using: $XRAY_BIN"
$XRAY_BIN version 2>/dev/null | head -1
echo ""

# === Step 2: Create client config ===
echo "--- Step 2: Creating client config ---"
cat > "$WORK_DIR/config.json" << XRAYEOF
{
    "log": {
        "loglevel": "debug",
        "access": "$WORK_DIR/access.log",
        "error": "$WORK_DIR/error.log"
    },
    "inbounds": [
        {
            "listen": "127.0.0.1",
            "port": $LOCAL_SOCKS_PORT,
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
                        "address": "$SERVER_IP",
                        "port": $SERVER_PORT,
                        "users": [
                            {
                                "id": "$UUID",
                                "encryption": "none",
                                "flow": "$FLOW"
                            }
                        ]
                    }
                ]
            },
            "streamSettings": {
                "network": "tcp",
                "security": "reality",
                "realitySettings": {
                    "serverName": "$SNI",
                    "fingerprint": "chrome",
                    "publicKey": "$PUBLIC_KEY",
                    "shortId": "$SHORT_ID",
                    "spiderX": "/"
                }
            },
            "tag": "proxy"
        },
        {
            "protocol": "freedom",
            "tag": "direct"
        }
    ]
}
XRAYEOF

echo "Config created at $WORK_DIR/config.json"
cat "$WORK_DIR/config.json" | python3 -c "import sys,json; json.load(sys.stdin); print('Config JSON: VALID')" 2>/dev/null || echo "Config JSON: INVALID"
echo ""

# === Step 3: Start Xray ===
echo "--- Step 3: Starting Xray ---"
"$XRAY_BIN" run -config "$WORK_DIR/config.json" &
XRAY_PID=$!
echo "Xray started (PID: $XRAY_PID)"
sleep 3

# Check if Xray is still running
if ! kill -0 $XRAY_PID 2>/dev/null; then
    echo "ERROR: Xray crashed on startup!"
    echo ""
    echo "=== Error Log ==="
    cat "$WORK_DIR/error.log" 2>/dev/null || echo "(no error log)"
    exit 1
fi
echo "Xray is running"
echo ""

# === Step 4: Test connectivity ===
echo "--- Step 4: Testing connectivity through tunnel ---"
echo ""

# Test 1: Basic SOCKS proxy connectivity
echo -n "Test A - SOCKS proxy listening: "
if nc -z 127.0.0.1 $LOCAL_SOCKS_PORT 2>/dev/null; then
    echo "OK"
else
    echo "FAIL - SOCKS port not open"
fi

# Test 2: Fetch IP through tunnel
echo -n "Test B - Fetch public IP through tunnel: "
TUNNEL_IP=$(curl -s --connect-timeout 15 -x socks5h://127.0.0.1:$LOCAL_SOCKS_PORT https://ifconfig.me 2>/dev/null)
if [ -n "$TUNNEL_IP" ]; then
    echo "SUCCESS - Tunnel IP: $TUNNEL_IP"
    if [ "$TUNNEL_IP" = "$SERVER_IP" ]; then
        echo "  -> Traffic exits from your server in Spain!"
    fi
else
    echo "FAIL - Could not fetch IP through tunnel"
fi

# Test 3: DNS resolution through tunnel
echo -n "Test C - DNS through tunnel (google.com): "
GOOGLE_RESULT=$(curl -s --connect-timeout 15 -x socks5h://127.0.0.1:$LOCAL_SOCKS_PORT https://www.google.com -o /dev/null -w "%{http_code}" 2>/dev/null)
if [ "$GOOGLE_RESULT" = "200" ] || [ "$GOOGLE_RESULT" = "302" ]; then
    echo "SUCCESS (HTTP $GOOGLE_RESULT)"
else
    echo "FAIL (HTTP $GOOGLE_RESULT)"
fi

# Test 4: Speed test (small download)
echo -n "Test D - Download test (small file): "
SPEED_RESULT=$(curl -s --connect-timeout 15 -x socks5h://127.0.0.1:$LOCAL_SOCKS_PORT -o /dev/null -w "%{speed_download}" https://www.google.com/robots.txt 2>/dev/null)
if [ -n "$SPEED_RESULT" ] && [ "$SPEED_RESULT" != "0.000" ]; then
    echo "SUCCESS (${SPEED_RESULT} bytes/sec)"
else
    echo "FAIL"
fi

echo ""

# === Step 5: Show logs ===
echo "--- Step 5: Xray Logs ---"
echo ""
echo "=== Access Log (last 20 lines) ==="
tail -20 "$WORK_DIR/access.log" 2>/dev/null || echo "(empty)"
echo ""
echo "=== Error Log (last 30 lines) ==="
tail -30 "$WORK_DIR/error.log" 2>/dev/null || echo "(empty)"
echo ""

echo "=== Test Complete ==="
echo ""
echo "If tests B-D failed, check the error log above for the specific failure reason."
echo "Common issues:"
echo "  - 'dial tcp ... connection refused': Server not accepting connections"
echo "  - 'reality: handshake failed': Reality key/shortId mismatch"
echo "  - 'context deadline exceeded': Connection timeout (GFW blocking?)"
echo "  - 'EOF': Connection reset by GFW or server"
