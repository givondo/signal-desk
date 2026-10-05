# Custom domain: `desktrading-today.precifarm.com`

Uses a **Cloudflare Named Tunnel** (HTTPS, no public port 8899 required).

## Prerequisites

- Domain **precifarm.com** (or parent zone) on [Cloudflare DNS](https://dash.cloudflare.com).
- Signal Desk running on the VM: `http://127.0.0.1:8899` (`signaldesk.service` active).

## 1. Create the tunnel (Cloudflare dashboard)

1. Open [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → **Networks** → **Tunnels**.
2. **Create a tunnel** → type **Cloudflared** → name e.g. `signaldesk-gcp`.
3. **Public Hostname** (add route):
   - **Subdomain:** `desktrading-today`
   - **Domain:** `precifarm.com`
   - **Service type:** HTTP
   - **URL:** `localhost:8899`
4. On **Install connector**, copy the **token** (starts with `eyJ…`).  
   Do not commit this token to git.

Cloudflare creates the DNS record for `desktrading-today.precifarm.com` automatically.

## 2. Install connector on the GCP VM

**Browser SSH** on the VM, or:

```powershell
& "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd" compute ssh signaldesk --zone=us-central1-a --project=skilled-orbit-460722-h9
```

On the VM:

```bash
sudo bash -c 'echo CLOUDFLARE_TUNNEL_TOKEN=PASTE_EYJ_TOKEN_HERE > /etc/cloudflared-signaldesk.env'
sudo chmod 600 /etc/cloudflared-signaldesk.env
curl -fsSL https://raw.githubusercontent.com/givondo/signal-desk/master/deploy/cloudflare-named-domain.sh | sudo bash
```

## 3. Verify

- https://desktrading-today.precifarm.com/api/health — no login, JSON `status: ok`
- https://desktrading-today.precifarm.com — login **trader** / your password

```bash
sudo systemctl status cloudflared-signaldesk
sudo journalctl -u cloudflared-signaldesk -n 30
```

## Optional: lock down GCP firewall

Once the tunnel works, you can remove public access to port **8899**:

- GCP → **VPC network** → **Firewall** → edit or delete `allow-signaldesk`,  
  **or** restrict source IPs to your office only.

The app stays reachable via Cloudflare HTTPS.

## Replace quick tunnel

If you previously ran `add-cloudflare.sh` (`*.trycloudflare.com`), `cloudflare-named-domain.sh` stops that service and replaces it with the named tunnel.

## Update app (unchanged)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/givondo/signal-desk/master/deploy/gcp-update-live.sh)
```

Tunnel service is independent; no need to recreate the token on app updates.
