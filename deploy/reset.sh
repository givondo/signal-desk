#!/usr/bin/env bash
# Fast, robust: reset password (no pipe traps) + one clean tunnel. Run as root.
set -eu

echo ">> Resetting password..."
PW=$(openssl rand -hex 8 2>/dev/null) || PW=$(date +%s%N | sha256sum | cut -c1-16)
printf 'SIGNALDESK_USER=trader\nSIGNALDESK_PASS=%s\nDATA_DIR=/opt/signaldesk/data\n' "$PW" \
  > /etc/signaldesk.env
chmod 600 /etc/signaldesk.env
systemctl restart signaldesk

echo ">> Clearing any stray tunnels..."
systemctl stop cloudflared-signaldesk 2>/dev/null || true
pkill -f 'cloudflared tunnel' 2>/dev/null || true
sleep 3

echo ">> Starting one clean tunnel..."
systemctl start cloudflared-signaldesk
URL=""
for i in $(seq 1 20); do
  URL=$(journalctl -u cloudflared-signaldesk --no-pager --since "40 sec ago" 2>/dev/null \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1) || true
  [ -n "$URL" ] && break
  sleep 2
done

echo
echo "=================================================="
echo "  User : trader"
echo "  Pass : $PW"
echo "  HTTPS: ${URL:-<pending - check again in a moment>}"
echo "  HTTP : http://136.113.168.166:8899   (always works)"
echo "=================================================="
echo "Save the password. Give the HTTPS URL ~30s to propagate."
