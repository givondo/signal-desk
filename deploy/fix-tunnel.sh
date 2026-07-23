#!/usr/bin/env bash
# Reconfigure the Cloudflare quick tunnel for reliability (http2 + IPv4 edge),
# then self-test that it actually serves. Run on the VM as root.
set -euo pipefail

echo ">> Reconfiguring tunnel (http2 + IPv4 edge - fixes most 1033s)..."
cat > /etc/systemd/system/cloudflared-signaldesk.service <<'EOF'
[Unit]
Description=Cloudflare quick tunnel for Signal Desk
After=network-online.target signaldesk.service
Wants=network-online.target

[Service]
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --protocol http2 --edge-ip-version 4 --url http://localhost:8899
Restart=always
RestartSec=8

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl restart cloudflared-signaldesk
sleep 3

echo ">> Waiting for the assigned URL..."
URL=""
for i in $(seq 1 45); do
  URL=$(journalctl -u cloudflared-signaldesk --no-pager --since "90 sec ago" 2>/dev/null \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
  [ -n "$URL" ] && break
  sleep 2
done

echo ">> Self-testing the tunnel from the VM..."
CODE="n/a"
if [ -n "$URL" ]; then
  sleep 6
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL/" || echo "no-response")
fi

echo
echo "======================================================"
echo "  local app      : $(systemctl is-active signaldesk)"
echo "  tunnel service : $(systemctl is-active cloudflared-signaldesk)"
echo "  HTTPS URL      : ${URL:-<none captured>}"
echo "  URL responds   : HTTP ${CODE}   (401 = WORKING, login required)"
echo "======================================================"
if [ "$CODE" != "401" ] && [ "$CODE" != "200" ]; then
  echo ">> Not serving yet - recent cloudflared logs:"
  journalctl -u cloudflared-signaldesk --no-pager | tail -22
fi
