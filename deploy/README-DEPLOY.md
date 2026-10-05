# Signal Desk → Oracle Always Free VM

The desk runs 24/7 on a $0-forever VM, reachable privately from your phone/PC
via Tailscale. No public exposure, no port opening, no domain needed.

## 1. Create the free Oracle account (~10 min, once)
1. Go to https://www.oracle.com/cloud/free/ → **Start for free**
2. Sign up. A **credit card is required for identity verification only** —
   Always Free resources never charge it. Pick a home region close to you.
3. If signup rejects you the first time, retry later — their fraud filter is
   notoriously trigger-happy. Nothing was charged.

## 2. Create the VM (Always Free shape)
1. Console → **Compute → Instances → Create instance**
2. Name: `signaldesk`
3. Image: **Ubuntu 24.04** (or 22.04)
4. Shape: click *Change shape* → **Ampere / VM.Standard.A1.Flex**
   → 1 OCPU, 6 GB RAM is plenty (Always Free allows up to 4 OCPU / 24 GB).
   If A1 capacity is unavailable, retry later or use **VM.Standard.E2.1.Micro**
   (also Always Free, weaker but sufficient).
5. Networking: defaults are fine. **Do NOT open port 8899 in the security
   list** — access is via Tailscale only, that's the point.
6. **Download the SSH private key** it generates (or paste your own public key).
7. Create. Note the **public IP** shown on the instance page.

## 3. Deploy (from this Windows PC)
Project folder: `C:\Users\DAVID\Desktop\06 - Trading`

**Option A — helper script** (after the VM has a public IP and you downloaded the `.key`):

    cd "C:\Users\DAVID\Desktop\06 - Trading\deploy"
    .\push-oracle.ps1 -VmIp YOUR_VM_PUBLIC_IP -KeyPath "C:\path\to\ssh-key.key"

**Option B — manual `scp`**:

    cd "C:\Users\DAVID\Desktop\06 - Trading"
    scp -i C:\path\to\ssh-key.key xauusd_trader.py dashboard.html `
        deploy\setup.sh deploy\signaldesk.service `
        predictions*.json tv_auth.json `
        ubuntu@VM_IP:~

Then connect and run setup:

    ssh -i C:\path\to\ssh-key.key ubuntu@VM_IP
    chmod +x setup.sh && ./setup.sh
    sudo tailscale up     # open the printed URL, sign in (same account)

Journal + TV session persist under **`/opt/signaldesk/data`** (`DATA_DIR`).

## 4. Point your phone at the VM
After `tailscale up`, the VM appears in your tailnet (e.g. `signaldesk`).
Phone/PC URL:

    http://signaldesk:8899        (MagicDNS)
    http://<vm-100.x-address>:8899

Re-pin the home-screen shortcut to this URL. Done — the PC no longer needs
to stay on.

## 5. Afterwards
- **Stop the PC copy** (optional): delete
  `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SignalDesk-autostart.bat`
  so two trackers don't build two diverging ledgers. The VM carried your
  history and TradingView session with it.
- Logs on the VM: `journalctl -u signaldesk -f`
- Update the app later: `scp` the new .py/.html up, then
  `sudo cp ~/xauusd_trader.py ~/dashboard.html /opt/signaldesk/ && sudo systemctl restart signaldesk`
- Extra lock (optional): edit `/etc/signaldesk.env` and add
  `SIGNALDESK_USER=trader` and `SIGNALDESK_PASS=...`, then
  `sudo systemctl restart signaldesk` → Basic auth even on the tailnet.
- Update after git pull: run `push-oracle.ps1` again, then on the VM:
  `sudo cp ~/xauusd_trader.py ~/dashboard.html /opt/signaldesk/ && sudo systemctl restart signaldesk`

## Oracle vs GCP?
| | **Oracle + Tailscale** (this doc) | **GCP e2-micro** (`README-GCP.md`) |
|--|--|--|
| Access | Private tailnet only | Public IP :8899 |
| Sleep | Never | Never |
| Journal | `/opt/signaldesk/data` | Same |
| Best for | Personal desk, phone via Tailscale | Quick public URL |

You can install **Tailscale on GCP too** and skip opening port 8899 — same privacy as Oracle.
