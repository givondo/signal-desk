#!/usr/bin/env bash
# Pull latest Signal Desk from GitHub onto the existing GCP VM (keeps password + journal).
# Run in Google Cloud Shell (project already set):
#   bash <(curl -fsSL https://raw.githubusercontent.com/givondo/signal-desk/master/deploy/gcp-update-live.sh)
set -euo pipefail

NAME=signaldesk
REPO=https://github.com/givondo/signal-desk.git
ZONE=$(gcloud compute instances list --filter="name=$NAME" \
        --format='value(zone)' 2>/dev/null | awk -F/ '{print $NF}' | head -n1)

if [ -z "${ZONE:-}" ]; then
  echo "No VM named $NAME. Run deploy/gcp-cloudshell.sh first."
  exit 1
fi

echo ">> Updating $NAME in $ZONE (password + /opt/signaldesk/data unchanged)..."

gcloud compute ssh "$NAME" --zone="$ZONE" --command="sudo bash -s" <<'REMOTE'
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq git >/dev/null 2>&1 || true
if [ -d /opt/signaldesk-src/.git ]; then
  cd /opt/signaldesk-src
  git fetch origin
  git checkout master
  git pull --ff-only origin master
else
  rm -rf /opt/signaldesk-src
  git clone https://github.com/givondo/signal-desk.git /opt/signaldesk-src
fi
mkdir -p /opt/signaldesk/data
cp /opt/signaldesk-src/xauusd_trader.py /opt/signaldesk-src/precifarm_analyst.py /opt/signaldesk-src/dashboard.html /opt/signaldesk/
cp /opt/signaldesk-src/deploy/signaldesk.service /etc/systemd/system/
systemctl daemon-reload
systemctl restart signaldesk
sleep 2
systemctl is-active signaldesk
curl -s localhost:8899/api/health
echo
REMOTE

IP=$(gcloud compute instances describe "$NAME" --zone="$ZONE" \
      --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
cat <<DONE

Updated. Same login as before.
  http://${IP}:8899
  Health: http://${IP}:8899/api/health
DONE
