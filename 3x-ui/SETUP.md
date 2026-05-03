# 3x-UI with VLESS-XTLS-Reality Setup Guide

## Quick Start

1. Start the container:
   ```bash
   cd /home/jon/homelab/3x-ui
   docker compose up -d
   ```

2. Access the panel at: `http://YOUR_SERVER_IP:2053`
   - Default username: `admin`
   - Default password: `admin`
   - **CHANGE THESE IMMEDIATELY**

## Initial Security Setup

1. **Change credentials**: Panel Settings → User Settings → Change username and password
2. **Change panel port**: Panel Settings → General → Panel Port (choose a non-standard port)
3. **Set panel URL path**: Panel Settings → General → Panel URL Root Path (e.g., `/secretpanel/`)
4. **Enable 2FA**: Panel Settings → Security Settings

## Configure VLESS-XTLS-Reality Inbound

1. Go to **Inbounds** → **Add Inbound**

2. Configure with these settings:

   | Setting | Value |
   |---------|-------|
   | Remark | Any name (e.g., "Reality") |
   | Protocol | `vless` |
   | Listen IP | (leave empty for 0.0.0.0) |
   | Port | `443` |
   | Total Traffic | 0 (unlimited) |

3. Under **Client** settings:
   - Email: Any identifier for the client
   - Flow: `xtls-rprx-vision`

4. Under **Transport** tab:
   - Transmission: `TCP`

5. Under **Security** tab:
   - Security: `Reality`
   - Click **Get New Cert** to generate x25519 keypair
   - uTLS: `chrome` (recommended)
   - Dest: `www.microsoft.com:443`
   - SNI: `www.microsoft.com`
   - Short IDs: Click generate or use default

6. Click **Create**

## Recommended SNI/Dest Targets

These sites work well for Reality camouflage:
- `www.microsoft.com:443` (most reliable)
- `www.apple.com:443`
- `dl.google.com:443`
- `www.amazon.com:443`
- `www.samsung.com:443`

**Selection criteria**: Must support TLSv1.3, HTTP/2, and should not redirect.

## Client Connection

After creating the inbound:
1. Click the QR code icon or copy the share link
2. Import into your VLESS client:
   - **iOS**: Streisand, V2Box, FoXray
   - **Android**: V2rayNG, NekoBox
   - **Windows**: V2rayN, Nekoray
   - **macOS**: V2rayU, FoXray

## Firewall Configuration

```bash
# Essential ports
sudo ufw allow 22/tcp     # SSH
sudo ufw allow 443/tcp    # VLESS-Reality inbound
sudo ufw allow 80/tcp     # ACME certificate challenges (optional)

# Restrict panel access to your IP only
sudo ufw allow from YOUR_IP to any port 2053
# Or if you changed the panel port:
# sudo ufw allow from YOUR_IP to any port YOUR_PANEL_PORT

sudo ufw enable
```

## Backup

The database is stored in `./db/x-ui.db`. Back it up regularly:
```bash
cp ./db/x-ui.db ./db/x-ui.db.backup.$(date +%Y%m%d)
```

## Troubleshooting

- **Can't access panel**: Check firewall rules, verify port 2053 is open
- **Reality handshake fails**: Verify SNI target supports TLSv1.3 with: `docker exec 3x-ui xray tls ping www.microsoft.com`
- **Slow speeds**: Try a different SNI target geographically closer to your server
- **Container logs**: `docker logs 3x-ui`
