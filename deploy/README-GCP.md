# Signal Desk → Google Cloud e2-micro (Always Free)

A real 24/7 VM that never sleeps. Free forever within Always Free limits.
Card required for signup verification only — not charged on Always Free.

## 1. Create the account (~10 min, once)
1. https://cloud.google.com/free → **Get started for free**
2. Sign in with Google, add a card (identity check; Always Free never charges it).

## 2. Create the Always Free VM
Console → **Compute Engine → VM instances → Create instance**
- **Name**: `signaldesk`
- **Region**: MUST be one of the free ones: **us-west1 / us-central1 / us-east1**
- **Machine type**: **e2-micro** (this is the Always Free shape — 2 vCPU shared, 1 GB RAM)
- **Boot disk**: Ubuntu 24.04 LTS, 30 GB Standard (free allowance is 30 GB)
- **Firewall**: tick **Allow HTTP traffic** (we'll add port 8899 next)
- **Create**. Note the **External IP** on the instance row.

## 3. Open the app port (one-time firewall rule)
Console → **VPC network → Firewall → Create firewall rule**
- Name: `allow-signaldesk`
- Direction: Ingress · Targets: All instances · Source ranges: `0.0.0.0/0`
- Protocols/ports: TCP **8899** → **Create**
(Access is still gated by the username+password you set in step 4.)

## 4. Deploy (from this Windows PC, PowerShell in C:\Users\DAVID\XAUUSD-Trader)
Easiest: use the browser **SSH** button on the VM row, then in that shell run
the block in step 5. To push files from here instead, install gcloud and:

    gcloud compute scp xauusd_trader.py dashboard.html `
        deploy/setup-gcp.sh deploy/signaldesk.service `
        signaldesk:~ --zone=<your-zone>

## 5. On the VM (browser SSH or gcloud ssh)
If you used the browser SSH button, first get the files there. Simplest is to
paste them, or clone from your GitHub repo:

    # option A - from your private GitHub repo (needs a token/login), or
    # option B - upload via the SSH window's gear -> Upload file, then:
    chmod +x setup-gcp.sh
    ./setup-gcp.sh          # prompts for the website password

That script installs Python, deploys to /opt/signaldesk, stores your password in
/etc/signaldesk.env, installs the systemd service (auto-start + auto-restart),
and points the prediction ledger at /opt/signaldesk/data (survives reboots).

## 6. Open your website
    http://<VM_EXTERNAL_IP>:8899
Browser asks for user `trader` + your password. Done — reachable from anywhere,
PC no longer needed.

## Notes
- **1 GB RAM** is plenty (the app uses ~60 MB).
- Plain HTTP means the password travels unencrypted. Fine for a personal tool;
  for HTTPS on a bare IP, front it with a **Cloudflare Tunnel** (free, no domain
  needed) or add a domain + Caddy. Ask and I'll wire it up.
- Logs: `journalctl -u signaldesk -f`
- Update later: re-upload the .py/.html to ~, then
  `sudo cp ~/xauusd_trader.py ~/dashboard.html /opt/signaldesk/ && sudo systemctl restart signaldesk`
- Stop the PC autostart afterward:
  delete `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SignalDesk-autostart.bat`
