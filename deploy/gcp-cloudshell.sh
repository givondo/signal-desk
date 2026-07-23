#!/usr/bin/env bash
# ============================================================================
# Signal Desk - one-shot deploy from Google Cloud Shell to a free e2-micro VM.
# Run in Cloud Shell:
#   bash <(curl -fsSL https://raw.githubusercontent.com/givondo/signal-desk/master/deploy/gcp-cloudshell.sh)
# Idempotent: safe to re-run (reuses the VM, just redeploys + restarts).
# ============================================================================
set -euo pipefail

NAME=signaldesk
REPO=https://github.com/givondo/signal-desk.git
ZONES=(us-central1-a us-west1-b us-east1-b)   # all Always-Free eligible

say(){ printf '\n\033[1;33m>> %s\033[0m\n' "$*"; }

# --- project ---------------------------------------------------------------
PROJECT=$(gcloud config get-value project 2>/dev/null || true)
if [ -z "$PROJECT" ] || [ "$PROJECT" = "(unset)" ]; then
  echo "No project set. Run:  gcloud config set project YOUR_PROJECT_ID"
  echo "List them with:       gcloud projects list"
  exit 1
fi
say "Project: $PROJECT"

# --- password --------------------------------------------------------------
read -rsp ">> Choose a website password (login user will be 'trader'): " PW; echo
[ -z "$PW" ] && { echo "Password cannot be empty."; exit 1; }

# --- firewall (idempotent) -------------------------------------------------
if ! gcloud compute firewall-rules describe allow-signaldesk >/dev/null 2>&1; then
  say "Opening TCP 8899 (access still gated by your password)"
  gcloud compute firewall-rules create allow-signaldesk \
    --allow=tcp:8899 --target-tags=signaldesk --source-ranges=0.0.0.0/0 -q
fi

# --- find or create the VM -------------------------------------------------
ZONE=$(gcloud compute instances list --filter="name=$NAME" \
        --format='value(zone)' 2>/dev/null | awk -F/ '{print $NF}' | head -n1)
if [ -n "${ZONE:-}" ]; then
  say "Reusing existing VM in $ZONE"
else
  created=no
  for Z in "${ZONES[@]}"; do
    say "Creating e2-micro (Always Free) in $Z ..."
    if gcloud compute instances create "$NAME" --zone="$Z" \
         --machine-type=e2-micro --image-family=ubuntu-2404-lts \
         --image-project=ubuntu-os-cloud --boot-disk-size=30GB \
         --boot-disk-type=pd-standard --tags=signaldesk -q; then
      ZONE=$Z; created=yes; break
    fi
    echo "   $Z had no capacity, trying the next free zone..."
  done
  [ "$created" = yes ] || { echo "All free zones were at capacity - retry later."; exit 1; }
fi

# --- provisioner (runs on the VM as root) ----------------------------------
cat > /tmp/provision.sh <<'PROV'
#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
# wait out the boot-time apt lock (cloud-init / unattended-upgrades)
for i in $(seq 1 60); do fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break; sleep 5; done
apt-get update -y
apt-get install -y python3 git
rm -rf /opt/signaldesk-src
git clone https://github.com/givondo/signal-desk.git /opt/signaldesk-src
mkdir -p /opt/signaldesk/data
cp /opt/signaldesk-src/xauusd_trader.py /opt/signaldesk-src/dashboard.html /opt/signaldesk/
cp /opt/signaldesk-src/deploy/signaldesk.service /etc/systemd/system/
mv /tmp/signaldesk.env /etc/signaldesk.env
chmod 600 /etc/signaldesk.env
systemctl daemon-reload
systemctl enable signaldesk
systemctl restart signaldesk
sleep 3
systemctl --no-pager status signaldesk | head -4
PROV

# secrets delivered over SSH (scp), never via instance metadata
cat > /tmp/signaldesk.env <<EOF
SIGNALDESK_USER=trader
SIGNALDESK_PASS=${PW}
DATA_DIR=/opt/signaldesk/data
EOF

# --- wait for SSH, then deploy ---------------------------------------------
say "Waiting for the VM to accept SSH (first key setup can take a minute)..."
until gcloud compute ssh "$NAME" --zone="$ZONE" -q --command=true >/dev/null 2>&1; do
  sleep 5
done

say "Uploading and running the provisioner..."
gcloud compute scp /tmp/provision.sh /tmp/signaldesk.env "$NAME":/tmp/ --zone="$ZONE" -q
gcloud compute ssh "$NAME" --zone="$ZONE" -q \
  --command="chmod +x /tmp/provision.sh && sudo /tmp/provision.sh"
rm -f /tmp/signaldesk.env

# --- done ------------------------------------------------------------------
IP=$(gcloud compute instances describe "$NAME" --zone="$ZONE" \
      --format='get(networkInterfaces[0].accessConfigs[0].natIP)')
cat <<DONE

======================================================
  SIGNAL DESK IS LIVE
  URL : http://${IP}:8899
  User: trader
  Pass: (the password you just set)
  Zone: ${ZONE}
======================================================
The app polls markets every 15s and auto-starts on reboot.
Logs:    gcloud compute ssh $NAME --zone=$ZONE --command='journalctl -u signaldesk -n 40'
Stop:    gcloud compute instances stop $NAME --zone=$ZONE
DONE
