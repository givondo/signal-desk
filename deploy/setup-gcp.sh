#!/usr/bin/env bash
# Signal Desk - Google Cloud e2-micro (Always Free), Ubuntu 22.04/24.04.
# Run on the VM from ~ after uploading files (see README-GCP.md).
set -euo pipefail

echo "== [1/5] Packages =="
sudo apt-get update -y
sudo apt-get install -y python3 curl

echo "== [2/5] Install app to /opt/signaldesk =="
sudo mkdir -p /opt/signaldesk/data
sudo cp xauusd_trader.py dashboard.html /opt/signaldesk/
for f in predictions*.json tv_auth.json; do
  [ -f "$f" ] && sudo cp "$f" /opt/signaldesk/data/ && echo "   carried $f → data/"
done
sudo chown -R root:root /opt/signaldesk

echo "== [3/5] Credentials + DATA_DIR =="
if [ -f /etc/signaldesk.env ]; then
  echo "   Keeping existing /etc/signaldesk.env (edit to change password)"
  grep -q DATA_DIR /etc/signaldesk.env || echo 'DATA_DIR=/opt/signaldesk/data' | sudo tee -a /etc/signaldesk.env >/dev/null
else
  read -rsp "Set website password (user 'trader'): " PW; echo
  [ -z "${PW}" ] && { echo "Password required for public GCP access."; exit 1; }
  sudo tee /etc/signaldesk.env >/dev/null <<EOF
SIGNALDESK_USER=trader
SIGNALDESK_PASS=${PW}
DATA_DIR=/opt/signaldesk/data
EOF
  sudo chmod 600 /etc/signaldesk.env
fi

echo "== [4/5] systemd =="
sudo cp signaldesk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now signaldesk
sleep 3
sudo systemctl --no-pager status signaldesk | head -6

echo "== [5/5] Health =="
curl -s localhost:8899/api/health; echo
echo
echo ">>> Browse: http://<VM_EXTERNAL_IP>:8899  (user: trader, your password)"
echo ">>> Ensure firewall rule allow-signaldesk (TCP 8899) — see README-GCP.md"
