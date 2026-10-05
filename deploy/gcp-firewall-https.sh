#!/usr/bin/env bash
# Open TCP 80/443 on GCP for Caddy / Let's Encrypt. Run in Cloud Shell or with gcloud on PC.
set -euo pipefail

NAME=allow-signaldesk-https
PROJECT=$(gcloud config get-value project 2>/dev/null || true)
[ -n "$PROJECT" ] || { echo "gcloud project not set"; exit 1; }

if gcloud compute firewall-rules describe "$NAME" >/dev/null 2>&1; then
  echo "Firewall rule $NAME already exists."
else
  gcloud compute firewall-rules create "$NAME" \
    --allow=tcp:80,tcp:443 \
    --source-ranges=0.0.0.0/0 \
    --description="Signal Desk HTTPS (Caddy + LE)"
  echo "Created $NAME"
fi
