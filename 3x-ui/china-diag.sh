#!/bin/bash
# China-side connectivity diagnostic for VLESS-Reality server
# Run this from your laptop in China

SERVER_IP="213.195.110.145"
DOMAIN="home.ashorkqueen.xyz"
PORT=443
SNI="www.nvidia.com"

echo "=== VLESS-Reality China Diagnostic ==="
echo "Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

# 1. Your public IP (to confirm you're in China)
echo "--- Test 1: Your public IP ---"
MY_IP=$(curl -s --connect-timeout 5 ifconfig.me 2>/dev/null || curl -s --connect-timeout 5 icanhazip.com 2>/dev/null || echo "FAILED")
echo "Your IP: $MY_IP"
echo ""

# 2. DNS resolution
echo "--- Test 2: DNS Resolution ---"
echo -n "System DNS:    "
dig +short $DOMAIN 2>/dev/null || nslookup $DOMAIN 2>/dev/null | grep -A1 'Name:' | grep Address | head -1 || echo "dig/nslookup not available"
echo -n "Google DNS:    "
dig +short $DOMAIN @8.8.8.8 2>/dev/null || echo "Cannot reach Google DNS (may be blocked)"
echo -n "Cloudflare DNS: "
dig +short $DOMAIN @1.1.1.1 2>/dev/null || echo "Cannot reach Cloudflare DNS (may be blocked)"
echo ""

# 3. TCP connectivity to server IP on port 443
echo "--- Test 3: TCP Connection (raw IP) ---"
echo -n "TCP to $SERVER_IP:$PORT ... "
if nc -z -w 5 $SERVER_IP $PORT 2>/dev/null; then
    echo "SUCCESS"
elif bash -c "echo >/dev/tcp/$SERVER_IP/$PORT" 2>/dev/null; then
    echo "SUCCESS (bash)"
elif curl -so /dev/null --connect-timeout 5 "https://$SERVER_IP:$PORT" -k 2>/dev/null; then
    echo "SUCCESS (curl)"
else
    echo "FAILED - Port 443 unreachable (GFW may be blocking)"
fi
echo ""

# 4. TCP connectivity to domain on port 443
echo "--- Test 4: TCP Connection (domain) ---"
echo -n "TCP to $DOMAIN:$PORT ... "
if nc -z -w 5 $DOMAIN $PORT 2>/dev/null; then
    echo "SUCCESS"
elif bash -c "echo >/dev/tcp/$DOMAIN/$PORT" 2>/dev/null; then
    echo "SUCCESS (bash)"
else
    echo "FAILED"
fi
echo ""

# 5. TLS handshake with Reality SNI (nvidia.com)
echo "--- Test 5: TLS Handshake (Reality SNI) ---"
echo -n "TLS to $SERVER_IP:$PORT with SNI=$SNI ... "
TLS_RESULT=$(echo | openssl s_client -connect $SERVER_IP:$PORT -servername $SNI 2>&1 | head -20)
if echo "$TLS_RESULT" | grep -q "CONNECTED"; then
    CERT_SUBJECT=$(echo "$TLS_RESULT" | grep -i "subject" | head -1)
    echo "CONNECTED"
    echo "  Certificate: $CERT_SUBJECT"
else
    echo "FAILED"
    echo "  Error: $(echo "$TLS_RESULT" | grep -i "error\|errno\|refused\|timeout" | head -1)"
fi
echo ""

# 6. TLS handshake with domain
echo "--- Test 6: TLS Handshake (domain) ---"
echo -n "TLS to $DOMAIN:$PORT with SNI=$SNI ... "
TLS_RESULT2=$(echo | openssl s_client -connect $DOMAIN:$PORT -servername $SNI 2>&1 | head -20)
if echo "$TLS_RESULT2" | grep -q "CONNECTED"; then
    CERT_SUBJECT2=$(echo "$TLS_RESULT2" | grep -i "subject" | head -1)
    echo "CONNECTED"
    echo "  Certificate: $CERT_SUBJECT2"
else
    echo "FAILED"
    echo "  Error: $(echo "$TLS_RESULT2" | grep -i "error\|errno\|refused\|timeout" | head -1)"
fi
echo ""

# 7. Traceroute (if available, limited hops)
echo "--- Test 7: Route to server (first 10 hops) ---"
if command -v traceroute &>/dev/null; then
    traceroute -m 10 -w 2 $SERVER_IP 2>/dev/null
elif command -v tracepath &>/dev/null; then
    tracepath -m 10 $SERVER_IP 2>/dev/null
else
    echo "traceroute not available"
fi
echo ""

# 8. Latency test
echo "--- Test 8: Latency ---"
echo -n "Ping $SERVER_IP ... "
PING_RESULT=$(ping -c 3 -W 5 $SERVER_IP 2>&1)
if echo "$PING_RESULT" | grep -q "time="; then
    echo "$PING_RESULT" | grep "rtt\|round-trip\|avg" | tail -1
else
    echo "FAILED (ICMP may be blocked)"
fi
echo ""

echo "=== Diagnostic Complete ==="
echo ""
echo "INTERPRETATION:"
echo "- If Test 3 FAILS: GFW is blocking TCP to your server IP on port 443"
echo "- If Test 3 passes but Test 5 FAILS: GFW is doing deep packet inspection on TLS"
echo "- If Test 5 passes but VPN still fails: Issue is in the VLESS/Reality protocol layer"
echo "- If Test 2 shows wrong IP: DNS poisoning by GFW"
