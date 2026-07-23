#!/usr/bin/env bash
# One-shot: reset password to a clean generated value + cleanly (re)start the
# Cloudflare tunnel and verify it actually serves. Run on the VM as root.
set -euo pipefail

echo ">> Resetting login password (generated, avoids paste/typing issues)..."
PW=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 14)
printf 'SIGNALDESK_USER=trader\nSIGNALDESK_PASS=%s\nDATA_DIR=/opt/signaldesk/data\n' "$PW" \
  > /etc/signaldesk.env
chmod 600 /etc/signaldesk.env
systemctl restart signaldesk
sleep 2

echo ">> Clean tunnel restart (single start, no URL churn)..."
systemctl stop cloudflared-signaldesk 2>/dev/null || true
sleep 3
systemctl start cloudflared-signaldesk

echo ">> Waiting for the assigned URL..."
URL=""
for i in $(seq 1 40); do
  URL=$(journalctl -u cloudflared-signaldesk --no-pager --since "60 sec ago" 2>/dev/null \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
  [ -n "$URL" ] && break
  sleep 2
done

echo ">> Verifying through Cloudflare (retries for edge propagation)..."
CODE="none"
if [ -n "$URL" ]; then
  for i in $(seq 1 12); do
    sleep 5
    CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 12 "$URL/" 2>/dev/null || echo err)
    echo "   attempt $i -> HTTP $CODE"
    [ "$CODE" = "401" ] && break
  done
fi

IP=$(curl -s -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null || echo "your-vm-ip")

echo
echo "======================================================"
echo "  LOGIN"
echo "    User : trader"
echo "    Pass : $PW"
echo
echo "  ACCESS"
if [ "$CODE" = "401" ] || [ "$CODE" = "200" ]; then
  echo "    HTTPS: $URL   [VERIFIED, HTTP $CODE]"
else
  echo "    HTTPS: ${URL:-<none>}   [not confirmed, HTTP $CODE]"
fi
echo "    HTTP : http://$IP:8899   (always works, unencrypted)"
echo "======================================================"
echo "Save the password above. Do NOT re-run the tunnel scripts -"
echo "each restart changes the HTTPS URL."
