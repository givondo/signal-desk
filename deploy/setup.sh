#!/usr/bin/env bash
# Signal Desk - Oracle Always Free VM (Ubuntu 22.04/24.04) + Tailscale
# Run from ~ after scp: xauusd_trader.py dashboard.html signaldesk.service setup.sh
set -euo pipefail

echo "== [1/5] System packages =="
sudo apt-get update -y
sudo apt-get install -y python3 curl

echo "== [2/5] Install app to /opt/signaldesk =="
sudo mkdir -p /opt/signaldesk/data
sudo cp xauusd_trader.py dashboard.html /opt/signaldesk/
for f in predictions*.json tv_auth.json; do
  [ -f "$f" ] && sudo cp "$f" /opt/signaldesk/data/ && echo "   carried $f → data/"
done
sudo chown -R root:root /opt/signaldesk

echo "== [3/5] Environment (persistent journal + optional auth) =="
if [ ! -f /etc/signaldesk.env ]; then
  read -rsp "Optional Basic auth password (Enter = skip, Tailscale-only): " PW
  echo
  if [ -n "${PW}" ]; then
    sudo tee /etc/signaldesk.env >/dev/null <<EOF
SIGNALDESK_USER=trader
SIGNALDESK_PASS=${PW}
DATA_DIR=/opt/signaldesk/data
EOF
  else
    sudo tee /etc/signaldesk.env >/dev/null <<EOF
DATA_DIR=/opt/signaldesk/data
EOF
    echo "   No password — reachable on tailnet without login prompt."
  fi
  sudo chmod 600 /etc/signaldesk.env
else
  echo "   Keeping existing /etc/signaldesk.env"
  grep -q DATA_DIR /etc/signaldesk.env || echo 'DATA_DIR=/opt/signaldesk/data' | sudo tee -a /etc/signaldesk.env >/dev/null
fi

echo "== [4/5] systemd service =="
sudo cp signaldesk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now signaldesk
sleep 3
sudo systemctl --no-pager status signaldesk | head -6

echo "== [5/5] Tailscale (private access — do NOT open port 8899 publicly) =="
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
echo
echo ">>> Run:  sudo tailscale up"
echo ">>> Sign in with the SAME Tailscale account as your PC/phone."
echo ">>> Then open:  http://<vm-hostname>:8899   (MagicDNS, e.g. http://signaldesk:8899)"
echo
curl -s localhost:8899/api/health | head -c 200 || true
echo
