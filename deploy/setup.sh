#!/usr/bin/env bash
# Signal Desk - Oracle Always Free VM setup (Ubuntu 22.04/24.04)
# Run from the directory containing: xauusd_trader.py dashboard.html
#                                    signaldesk.service (+ optional *.json state)
set -euo pipefail

echo "== [1/4] System packages =="
sudo apt-get update -y
sudo apt-get install -y python3 curl

echo "== [2/4] Install app to /opt/signaldesk =="
sudo mkdir -p /opt/signaldesk
sudo cp xauusd_trader.py dashboard.html /opt/signaldesk/
# carry over prediction history + TradingView session if provided
for f in predictions*.json tv_auth.json; do
  [ -f "$f" ] && sudo cp "$f" /opt/signaldesk/ && echo "   carried $f"
done
sudo chown -R root:root /opt/signaldesk
sudo chmod 600 /opt/signaldesk/tv_auth.json 2>/dev/null || true

echo "== [3/4] systemd service (auto-start + auto-restart) =="
sudo cp signaldesk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now signaldesk
sleep 3
sudo systemctl --no-pager status signaldesk | head -5

echo "== [4/4] Tailscale (private access, no public exposure) =="
curl -fsSL https://tailscale.com/install.sh | sh
echo
echo ">>> Now run:  sudo tailscale up"
echo ">>> Open the printed login URL in any browser, sign in with the SAME"
echo ">>> account as your PC/phone, then the desk is at:"
echo ">>>   http://<this-vm-tailscale-name>:8899"
echo
echo "Done. Check the app:  curl -s localhost:8899/api/signal?sym=XAUUSD | head -c 200"
