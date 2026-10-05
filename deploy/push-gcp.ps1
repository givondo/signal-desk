# Upload Signal Desk to a GCP VM (requires gcloud CLI + prior: gcloud auth login)
# Usage:
#   .\push-gcp.ps1 -Zone us-central1-a -Instance signaldesk
param(
  [Parameter(Mandatory = $true)]
  [string] $Zone,
  [string] $Instance = "signaldesk"
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
  throw "gcloud not found. Install: https://cloud.google.com/sdk/docs/install — or use Cloud Shell (README-GCP.md)."
}

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$staging = Join-Path $env:TEMP "signaldesk-upload"
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging | Out-Null

Copy-Item (Join-Path $Root "xauusd_trader.py") $staging
Copy-Item (Join-Path $Root "dashboard.html") $staging
Copy-Item (Join-Path $Root "deploy\setup-gcp.sh") $staging
Copy-Item (Join-Path $Root "deploy\signaldesk.service") $staging
Get-ChildItem (Join-Path $Root "predictions*.json") -ErrorAction SilentlyContinue | Copy-Item -Destination $staging
if (Test-Path (Join-Path $Root "tv_auth.json")) {
  Copy-Item (Join-Path $Root "tv_auth.json") $staging
}

Write-Host "Uploading to ${Instance} (${Zone}) ..."
gcloud compute scp --recurse "$staging\*" "${Instance}:~/" --zone=$Zone

Write-Host @"

SSH in and install:
  gcloud compute ssh $Instance --zone=$Zone
  chmod +x setup-gcp.sh && ./setup-gcp.sh

"@
