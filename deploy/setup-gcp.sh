#!/usr/bin/env bash
# Signal Desk - Google Cloud e2-micro (Always Free) setup, Ubuntu 22.04/24.04.
# Run from the dir holding: xauusd_trader.py dashboard.html
#                           signaldesk.service setup-gcp.sh
set -euo pipefail

echo "== [1/4] Packages =="
sudo apt-get update -y
sudo apt-get install -y python3 curl

echo "== [2/4] Install app to /opt/signaldesk =="
sudo mkdir -p /opt/signaldesk
sudo cp xauusd_trader.py dashboard.html /opt/signaldesk/
sudo mkdir -p /opt/signaldesk/data           # persistent ledger (30GB disk)
sudo chown -R root:root /opt/signaldesk

echo "== [3/4] Credentials + service =="
# Prompt for the website password (this + user 'trader' gates the public site)
read -rsp "Set website password (SIGNALDESK_PASS): " PW; echo
sudo tee /etc/signaldesk.env >/dev/null <<EOF
SIGNALDESK_USER=trader
SIGNALDESK_PASS=${PW}
DATA_DIR=/opt/signaldesk/data
EOF
sudo chmod 600 /etc/signaldesk.env
sudo cp signaldesk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now signaldesk
sleep 3
sudo systemctl --no-pager status signaldesk | head -6

echo "== [4/4] Health check =="
curl -s -u "trader:${PW}" localhost:8899/api/signal?sym=XAUUSD | head -c 160; echo
echo
echo ">>> App is up on port 8899, behind Basic auth."
echo ">>> Open port 8899 to the internet from the GCP console (see README-GCP.md),"
echo ">>> then browse:  http://<VM_EXTERNAL_IP>:8899   (user: trader)"
