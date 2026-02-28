# US Intraday ORB Scanner — Deployment & Maintenance Guide

## Overview

| Item | Value |
|------|-------|
| App | US Intraday ORB Scanner (NASDAQ 100) |
| Server | Vultr VPS — `65.20.91.230` (hostname: `nse-screener`) |
| Repo path | `/opt/US_Intraday_ORB` |
| Branch | `production` |
| Port | `5002` |
| Cloudflare tunnel | `cloudflared-orb.service` (quick tunnel on trycloudflare.com) |

---

## Initial Setup (Already Done)

### 1. Clone repo on Vultr
```bash
cd /opt
git clone https://github.com/gsimhadri-ca/US_Intraday_ORB.git
cd US_Intraday_ORB
git checkout production
```

### 2. Create venv and install dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Start the app
```bash
# Foreground (testing only)
python3 app.py

# Background (persistent)
nohup python3 app.py > /tmp/orb.log 2>&1 &
```

### 4. Cloudflare Tunnel (quick tunnel)
Created a systemd service at `/etc/systemd/system/cloudflared-orb.service`:

```ini
[Unit]
Description=Cloudflare Quick Tunnel ORB
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/cloudflared tunnel --url http://localhost:5002
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable cloudflared-orb
systemctl start cloudflared-orb
```

---

## Getting the Current Cloudflare URL

The quick tunnel URL **changes every time the service restarts**. To find the current URL:

```bash
journalctl -u cloudflared-orb --no-pager | grep -i "trycloudflare"
```

Look for a line like:
```
INF |  https://xxxx-xxxx-xxxx-xxxx.trycloudflare.com
```

That is your current public URL. Open it on mobile or any browser.

---

## Maintenance Guide

### When Cloudflare URL Changes
The URL changes automatically on every restart of `cloudflared-orb.service` (e.g., server reboot, service restart).

**Steps to get the new URL:**
```bash
# SSH into Vultr
ssh root@65.20.91.230

# Get the new URL
journalctl -u cloudflared-orb --no-pager | grep -i "trycloudflare"
```

Share the new URL wherever needed (mobile bookmark, team, etc.).

**To avoid URL changes** — upgrade to a named Cloudflare tunnel with your own domain (permanent URL). See section below.

---

### Restarting the App

If the ORB app crashes or stops:
```bash
# Check if running
ps aux | grep app.py

# View logs
tail -f /tmp/orb.log

# Restart
cd /opt/US_Intraday_ORB
nohup python3 app.py > /tmp/orb.log 2>&1 &
```

### Restarting the Tunnel
```bash
systemctl restart cloudflared-orb

# Then get the new URL
journalctl -u cloudflared-orb --no-pager | grep -i "trycloudflare"
```

### Checking Service Status
```bash
systemctl status cloudflared-orb    # tunnel status
ps aux | grep app.py                # app process
```

---

## Deploying Updates

```bash
ssh root@65.20.91.230
cd /opt/US_Intraday_ORB
git pull origin production

# Kill old process and restart
pkill -f app.py
nohup python3 app.py > /tmp/orb.log 2>&1 &
```

---

## Related Services (same Vultr server)

| Service | Port | Systemd Unit | Tunnel |
|---------|------|--------------|--------|
| NSE OI Order Executor | 5001 | `order-executor.service` | `cloudflared-tunnel.service` |
| US ORB Scanner | 5002 | *(manual / nohup)* | `cloudflared-orb.service` |

> The NSE OI Screener and Orchestrator run on **Windows (D:\Trading\production)** via NSSM — not on this server.

---

## Optional: Permanent Cloudflare URL (Named Tunnel)

To get a stable URL that never changes:
1. Log into [Cloudflare Dashboard](https://dash.cloudflare.com) → Zero Trust → Tunnels
2. Create a named tunnel (or convert existing)
3. Add a public hostname (e.g., `orb.yourdomain.com`) → service `http://localhost:5002`
4. Update `/etc/systemd/system/cloudflared-orb.service` to use the named tunnel credentials
5. `systemctl restart cloudflared-orb`

No more URL hunting after restarts.
