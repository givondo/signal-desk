# Signal Desk → Google Cloud (Always Free e2-micro)

24/7 VM, persistent journal in `/opt/signaldesk/data`, Basic auth on the site.

## Fastest: Cloud Shell (no install on your PC)

1. Create a GCP account: https://cloud.google.com/free  
2. Open the console → click **Activate Cloud Shell** (terminal icon, top right).  
3. Set your project if needed:
   ```bash
   gcloud config set project YOUR_PROJECT_ID
   ```
4. Run the one-shot installer (creates VM, firewall, deploys from GitHub):
   ```bash
   bash <(curl -fsSL https://raw.githubusercontent.com/givondo/signal-desk/master/deploy/gcp-cloudshell.sh)
   ```
5. Choose a password when prompted. The script prints your URL:
   `http://EXTERNAL_IP:8899` — login **trader** / your password.

Re-run the same command anytime to redeploy the latest `master` from GitHub.

---

## Manual: create the VM yourself

### 1. VM
Console → **Compute Engine → VM instances → Create**
- **Name**: `signaldesk`
- **Region**: **us-central1**, **us-west1**, or **us-east1** (Always Free)
- **Machine type**: **e2-micro**
- **Image**: Ubuntu 24.04 LTS, 30 GB standard disk
- **Firewall**: optional “Allow HTTP” (we use port **8899** below)
- Create → note **External IP** and **Zone** (e.g. `us-central1-a`)

### 2. Firewall (once per project)
**VPC network → Firewall → Create rule**
- Name: `allow-signaldesk`
- Ingress · Targets: **All instances** (or network tag `signaldesk`)
- Source: `0.0.0.0/0` · TCP **8899**

### 3. Deploy files

**Option A — Browser SSH** (no gcloud on Windows)  
On the VM row → **SSH**. In the VM:
```bash
git clone https://github.com/givondo/signal-desk.git
cd signal-desk/deploy
chmod +x setup-gcp.sh
cp setup-gcp.sh signaldesk.service ../
cd ..
./deploy/setup-gcp.sh
```
Or upload `predictions*.json` / `tv_auth.json` via SSH **gear → Upload file** into `~` before running setup.

**Option B — gcloud from Windows**  
Project folder: `C:\Users\DAVID\Desktop\06 - Trading`
```powershell
cd deploy
.\push-gcp.ps1 -Zone us-central1-a
# then SSH as printed
```

### 4. Use the desk
`http://<EXTERNAL_IP>:8899` — user **trader**, password from setup.

Public health (no login): `http://<IP>:8899/api/health`

---

## Operations

| Task | Command (on VM or via gcloud ssh) |
|------|-----------------------------------|
| Logs | `journalctl -u signaldesk -f` |
| Restart | `sudo systemctl restart signaldesk` |
| Update app | Re-run Cloud Shell script, or `git pull` in `/opt/signaldesk-src` and copy `.py`/`.html` to `/opt/signaldesk/` |

Stop local PC duplicate (optional): remove any SignalDesk autostart batch from Windows Startup.

---

## Custom HTTPS domain (Precifarm)

Fixed URL **`https://desktrading-today.precifarm.com`** via Cloudflare Named Tunnel:  
see [README-CLOUDFLARE-DOMAIN.md](./README-CLOUDFLARE-DOMAIN.md).

---

## Optional: private access (Tailscale)

Instead of exposing 8899 to `0.0.0.0/0`, skip the public firewall rule and on the VM:
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```
Use `http://signaldesk:8899` on devices on your tailnet.

---

## Oracle alternative

Private-by-default VM: see [README-DEPLOY.md](./README-DEPLOY.md) (Oracle + Tailscale).
