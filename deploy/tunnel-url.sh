#!/usr/bin/env bash
# Restart the Cloudflare quick tunnel and print the CURRENT https URL.
# Quick-tunnel URLs change on every restart, so always read the fresh one.
# Run on the VM as root (via: curl ... | sudo bash)
set -euo pipefail

echo ">> Restarting tunnel for a clean URL..."
systemctl restart cloudflared-signaldesk
sleep 2

URL=""
for i in $(seq 1 45); do
  URL=$(journalctl -u cloudflared-signaldesk --no-pager --since "90 sec ago" 2>/dev/null \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
  [ -n "$URL" ] && break
  sleep 2
done

STATE=$(systemctl is-active cloudflared-signaldesk 2>/dev/null || echo unknown)
echo
echo "======================================================"
echo "  tunnel service : $STATE"
if [ -n "$URL" ]; then
  echo "  HTTPS URL      : $URL"
  echo "  Login          : trader  +  your password"
else
  echo "  No URL captured. Recent logs:"
  journalctl -u cloudflared-signaldesk --no-pager | tail -14
fi
echo "======================================================"
