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
Open PowerShell in `C:\Users\DAVID\XAUUSD-Trader` and copy the app + state up
(replace VM_IP and the key path):

    scp -i C:\path\to\ssh-key.key xauusd_trader.py dashboard.html `
        predictions*.json tv_auth.json deploy\setup.sh deploy\signaldesk.service `
        ubuntu@VM_IP:~

Then connect and run setup:

    ssh -i C:\path\to\ssh-key.key ubuntu@VM_IP
    chmod +x setup.sh && ./setup.sh
    sudo tailscale up     # open the printed URL, sign in (same account)

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
- Extra lock (optional): uncomment `SIGNALDESK_PASS` in
  `/etc/systemd/system/signaldesk.service`, then
  `sudo systemctl daemon-reload && sudo systemctl restart signaldesk`
  → browser will ask user/password even inside the tailnet.
