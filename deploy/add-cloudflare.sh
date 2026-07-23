#!/usr/bin/env bash
# ============================================================================
# Add a free Cloudflare Quick Tunnel in front of Signal Desk -> HTTPS URL.
# No domain, no Cloudflare account needed. Run ON the VM as root:
#   curl -fsSL https://raw.githubusercontent.com/givondo/signal-desk/master/deploy/add-cloudflare.sh | sudo bash
# ============================================================================
set -euo pipefail

ARCH=$(dpkg --print-architecture)   # amd64 on e2-micro

echo ">> Installing cloudflared ($ARCH)..."
curl -fsSL -o /tmp/cloudflared.deb \
  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"
dpkg -i /tmp/cloudflared.deb || apt-get install -f -y
rm -f /tmp/cloudflared.deb

echo ">> Installing tunnel service..."
cat > /etc/systemd/system/cloudflared-signaldesk.service <<'EOF'
[Unit]
Description=Cloudflare quick tunnel for Signal Desk
After=network-online.target signaldesk.service
Wants=network-online.target

[Service]
# Quick tunnel: free, no domain. Cloudflare assigns a *.trycloudflare.com URL.
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --url http://localhost:8899
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cloudflared-signaldesk >/dev/null 2>&1
systemctl restart cloudflared-signaldesk

echo ">> Waiting for Cloudflare to assign the HTTPS URL..."
URL=""
for i in $(seq 1 40); do
  URL=$(journalctl -u cloudflared-signaldesk --no-pager 2>/dev/null \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
  [ -n "$URL" ] && break
  sleep 2
done

echo
echo "======================================================"
if [ -n "$URL" ]; then
  echo "  HTTPS URL : $URL"
  echo "  Login     : trader  +  your existing password"
  echo "  (the plain http://IP:8899 still works too)"
else
  echo "  Tunnel is starting. Fetch the URL in a moment with:"
  echo "  sudo journalctl -u cloudflared-signaldesk | grep -oE 'https://[a-z0-9-]+\\.trycloudflare\\.com' | tail -1"
fi
echo "======================================================"
