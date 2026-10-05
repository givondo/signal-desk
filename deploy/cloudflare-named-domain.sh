#!/usr/bin/env bash
# Named Cloudflare Tunnel -> fixed HTTPS hostname (e.g. desktrading-today.precifarm.com).
# Requires precifarm.com on Cloudflare and a tunnel token from the dashboard.
#
# One-time: create tunnel in Cloudflare Zero Trust, add public hostname -> http://localhost:8899,
# copy the "Install connector" token, then on the VM:
#   echo 'CLOUDFLARE_TUNNEL_TOKEN=eyJ...' | sudo tee /etc/cloudflared-signaldesk.env
#   sudo chmod 600 /etc/cloudflared-signaldesk.env
#   curl -fsSL .../cloudflare-named-domain.sh | sudo bash
#
# Or: sudo CLOUDFLARE_TUNNEL_TOKEN='eyJ...' bash cloudflare-named-domain.sh
set -euo pipefail

HOSTNAME="${SIGNALDESK_PUBLIC_HOST:-desktrading-today.precifarm.com}"
ENV_FILE=/etc/cloudflared-signaldesk.env
ARCH=$(dpkg --print-architecture)

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Run as root (sudo)."
  exit 1
fi

if [ -z "${CLOUDFLARE_TUNNEL_TOKEN:-}" ] && [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a && source "$ENV_FILE" && set +a
fi

if [ -z "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]; then
  cat <<EOF
Missing CLOUDFLARE_TUNNEL_TOKEN.

Cloudflare dashboard (one time):
  1. https://one.dash.cloudflare.com/ → Networks → Tunnels → Create tunnel
  2. Name: signaldesk-gcp (Cloudflared connector)
  3. Public Hostname:
       Subdomain: desktrading-today   Domain: precifarm.com
       Service: HTTP → localhost:8899
  4. Copy the install token (long eyJ... string)

On this VM:
  echo 'CLOUDFLARE_TUNNEL_TOKEN=PASTE_TOKEN' | tee $ENV_FILE
  chmod 600 $ENV_FILE
  bash $(basename "$0")

Expected URL: https://${HOSTNAME}
EOF
  exit 1
fi

if ! grep -q '^CLOUDFLARE_TUNNEL_TOKEN=' "$ENV_FILE" 2>/dev/null; then
  umask 077
  printf 'CLOUDFLARE_TUNNEL_TOKEN=%s\n' "$CLOUDFLARE_TUNNEL_TOKEN" >"$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

echo ">> Installing cloudflared ($ARCH)..."
curl -fsSL -o /tmp/cloudflared.deb \
  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"
dpkg -i /tmp/cloudflared.deb || apt-get install -f -y
rm -f /tmp/cloudflared.deb

echo ">> Stopping quick-tunnel service (if any)..."
systemctl stop cloudflared-signaldesk 2>/dev/null || true
systemctl disable cloudflared-signaldesk 2>/dev/null || true
pkill -f 'cloudflared tunnel --no-autoupdate --url' 2>/dev/null || true

echo ">> Installing named tunnel service -> https://${HOSTNAME}"
cat >/etc/systemd/system/cloudflared-signaldesk.service <<EOF
[Unit]
Description=Cloudflare named tunnel for Signal Desk (${HOSTNAME})
After=network-online.target signaldesk.service
Wants=network-online.target

[Service]
EnvironmentFile=${ENV_FILE}
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --protocol http2 --edge-ip-version 4 run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cloudflared-signaldesk >/dev/null
systemctl restart cloudflared-signaldesk

echo ">> Waiting for connector..."
for i in $(seq 1 30); do
  if systemctl is-active cloudflared-signaldesk >/dev/null 2>&1 \
     && journalctl -u cloudflared-signaldesk --no-pager --since "2 min ago" 2>/dev/null \
        | grep -qiE 'Registered tunnel connection|Connection.*registered'; then
    break
  fi
  sleep 2
done

echo
echo "======================================================"
echo "  HTTPS URL : https://${HOSTNAME}"
echo "  Health    : https://${HOSTNAME}/api/health"
echo "  Login     : trader + password from /etc/signaldesk.env"
echo "  Service   : $(systemctl is-active cloudflared-signaldesk || true)"
echo "======================================================"
journalctl -u cloudflared-signaldesk --no-pager -n 8 || true
