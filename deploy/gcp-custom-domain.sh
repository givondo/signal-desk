#!/usr/bin/env bash
# HTTPS custom domain on GCP VM (Caddy + Let's Encrypt). No Cloudflare required.
# DNS must point to this VM before certs issue:
#   desktrading-today.precifarm.com  A  ->  <EXTERNAL_IP>
#
# Run on the VM as root:
#   curl -fsSL https://raw.githubusercontent.com/givondo/signal-desk/master/deploy/gcp-custom-domain.sh | sudo bash
#
# Optional env:
#   SIGNALDESK_DOMAIN=desktrading-today.precifarm.com
set -euo pipefail

DOMAIN="${SIGNALDESK_DOMAIN:-desktrading-today.precifarm.com}"
UPSTREAM="${SIGNALDESK_UPSTREAM:-127.0.0.1:8899}"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Run as root (sudo)."
  exit 1
fi

echo ">> Domain: https://${DOMAIN} -> http://${UPSTREAM}"

echo ">> Installing Caddy..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -qq
apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl ca-certificates

if ! command -v caddy >/dev/null 2>&1; then
  curl -fsSL "https://dl.cloudsmith.io/public/caddy/stable/gpg.key" \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null || true
  curl -fsSL "https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt" \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -y -qq
  apt-get install -y -qq caddy
fi

echo ">> Caddyfile"
cat >/etc/caddy/Caddyfile <<EOF
${DOMAIN} {
    reverse_proxy ${UPSTREAM}
}
EOF

systemctl enable caddy >/dev/null 2>&1 || true
systemctl reload caddy 2>/dev/null || systemctl restart caddy

echo ">> Waiting for Caddy (TLS may fail until DNS points here)..."
sleep 3
systemctl is-active caddy || { journalctl -u caddy --no-pager -n 20; exit 1; }

IP=$(curl -fsS -4 --max-time 5 ifconfig.me 2>/dev/null || curl -fsS -4 --max-time 5 icanhazip.com 2>/dev/null || true)

cat <<DONE

======================================================
  Target URL : https://${DOMAIN}
  Health     : https://${DOMAIN}/api/health
  Login      : trader + password in /etc/signaldesk.env

  DNS (at precifarm.com registrar / DNS host):
    Type A   Name desktrading-today   Value ${IP:-YOUR_VM_EXTERNAL_IP}

  GCP firewall (once per project, from Cloud Shell or PC):
    gcloud compute firewall-rules create allow-signaldesk-https \\
      --allow=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 \\
      --description="Signal Desk HTTPS (Caddy)"

  Plain IP still works: http://${IP:-34.72.93.156}:8899
======================================================
DONE

journalctl -u caddy --no-pager -n 12 || true
