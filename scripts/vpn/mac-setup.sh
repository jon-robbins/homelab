#!/usr/bin/env bash
# mac-setup.sh — Configure Xray (Homebrew) as a persistent VLESS-Reality proxy on macOS
# Safe to re-run (idempotent).
set -euo pipefail

XRAY_BIN="/opt/homebrew/bin/xray"
XRAY_CONFIG="/opt/homebrew/etc/xray/config.json"
NETWORK_SERVICE="Wi-Fi"
SOCKS_PORT=10808
HTTP_PORT=10809

# ── Preflight ─────────────────────────────────────────────────────────────────
if [[ ! -x "$XRAY_BIN" ]]; then
  echo "❌  Xray not found at $XRAY_BIN"
  echo "   Install it first:  brew install xray"
  exit 1
fi

echo "✅  Xray found: $($XRAY_BIN version | head -1)"

# ── 1. Write Xray client config ──────────────────────────────────────────────
echo "📝  Writing config to $XRAY_CONFIG …"
mkdir -p "$(dirname "$XRAY_CONFIG")"

cat > "$XRAY_CONFIG" <<'EOF'
{
    "log": {
        "loglevel": "warning"
    },
    "inbounds": [
        {
            "listen": "127.0.0.1",
            "port": 10808,
            "protocol": "socks",
            "settings": {
                "udp": true
            },
            "tag": "socks-in"
        },
        {
            "listen": "127.0.0.1",
            "port": 10809,
            "protocol": "http",
            "settings": {},
            "tag": "http-in"
        }
    ],
    "outbounds": [
        {
            "protocol": "vless",
            "settings": {
                "vnext": [
                    {
                        "address": "213.195.110.145",
                        "port": 443,
                        "users": [
                            {
                                "id": "7fc5ddce-e5f1-49ad-9f90-1da0e6b7571e",
                                "encryption": "none",
                                "flow": "xtls-rprx-vision"
                            }
                        ]
                    }
                ]
            },
            "streamSettings": {
                "network": "tcp",
                "security": "reality",
                "realitySettings": {
                    "serverName": "www.nvidia.com",
                    "fingerprint": "chrome",
                    "publicKey": "88sO7yXUW9yN3QQRfTjKB8qwCG0_9oBsM-yWNBXqmGE",
                    "shortId": "01917a7976",
                    "spiderX": "/"
                }
            },
            "tag": "proxy"
        },
        {
            "protocol": "freedom",
            "tag": "direct"
        }
    ],
    "routing": {
        "domainStrategy": "AsIs",
        "rules": [
            {
                "type": "field",
                "ip": ["geoip:private"],
                "outboundTag": "direct"
            },
            {
                "type": "field",
                "domain": ["geosite:cn"],
                "outboundTag": "direct"
            }
        ]
    }
}
EOF

echo "   ✅  Config written."

# ── 2. Start / restart Xray ──────────────────────────────────────────────────
echo "🔄  Restarting xray via Homebrew services …"
brew services restart xray

echo "⏳  Waiting 2 seconds for xray to start …"
sleep 2

# Verify xray is running
if pgrep -x xray > /dev/null 2>&1; then
  echo "   ✅  Xray is running (PID $(pgrep -x xray))."
else
  echo "❌  Xray does not appear to be running."
  echo "   Check logs:  brew services info xray"
  exit 1
fi

# ── 3. Test connectivity ─────────────────────────────────────────────────────
echo ""
echo "🧪  Testing SOCKS5 proxy (127.0.0.1:$SOCKS_PORT) …"
if curl -s --max-time 10 --socks5-hostname "127.0.0.1:$SOCKS_PORT" https://www.google.com/generate_204 -o /dev/null -w "%{http_code}" | grep -q "204"; then
  echo "   ✅  SOCKS5 proxy works!"
else
  echo "   ⚠️  SOCKS5 test failed (may still work — Google could be unreachable from this network)."
fi

echo "🧪  Testing HTTP proxy (127.0.0.1:$HTTP_PORT) …"
if curl -s --max-time 10 -x "http://127.0.0.1:$HTTP_PORT" https://www.google.com/generate_204 -o /dev/null -w "%{http_code}" | grep -q "204"; then
  echo "   ✅  HTTP proxy works!"
else
  echo "   ⚠️  HTTP test failed (may still work — Google could be unreachable from this network)."
fi

# ── 4. Configure macOS system proxy ──────────────────────────────────────────
echo ""
echo "🌐  Configuring macOS system proxy on \"$NETWORK_SERVICE\" …"

# SOCKS proxy
networksetup -setsocksfirewallproxy "$NETWORK_SERVICE" 127.0.0.1 "$SOCKS_PORT"
networksetup -setsocksfirewallproxystate "$NETWORK_SERVICE" on
echo "   ✅  SOCKS proxy  → 127.0.0.1:$SOCKS_PORT"

# HTTP proxy
networksetup -setwebproxy "$NETWORK_SERVICE" 127.0.0.1 "$HTTP_PORT"
networksetup -setwebproxystate "$NETWORK_SERVICE" on
echo "   ✅  HTTP proxy   → 127.0.0.1:$HTTP_PORT"

# HTTPS proxy
networksetup -setsecurewebproxy "$NETWORK_SERVICE" 127.0.0.1 "$HTTP_PORT"
networksetup -setsecurewebproxystate "$NETWORK_SERVICE" on
echo "   ✅  HTTPS proxy  → 127.0.0.1:$HTTP_PORT"

# ── 5. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🎉  All done!  Your Mac is now routing traffic through the tunnel."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Useful commands:"
echo "    brew services start xray     # start proxy"
echo "    brew services stop xray      # stop proxy"
echo "    brew services restart xray   # restart proxy"
echo "    brew services info xray      # check status / logs"
echo ""
echo "  Quick on/off aliases — add these to your ~/.zshrc or ~/.bash_profile:"
echo ""
echo "    alias proxy-on='networksetup -setsocksfirewallproxystate \"Wi-Fi\" on && networksetup -setwebproxystate \"Wi-Fi\" on && networksetup -setsecurewebproxystate \"Wi-Fi\" on && echo \"Proxy ON\"'"
echo "    alias proxy-off='networksetup -setsocksfirewallproxystate \"Wi-Fi\" off && networksetup -setwebproxystate \"Wi-Fi\" off && networksetup -setsecurewebproxystate \"Wi-Fi\" off && echo \"Proxy OFF\"'"
echo ""
echo "  To disable the system proxy without stopping xray:"
echo "    proxy-off              (if you added the alias)"
echo "    — or manually in System Settings → Network → Wi-Fi → Proxies"
echo ""
echo "  Config file: $XRAY_CONFIG"
echo "  SOCKS5:      127.0.0.1:$SOCKS_PORT"
echo "  HTTP:        127.0.0.1:$HTTP_PORT"
echo ""
