# Copy Signal Desk to an Oracle Ubuntu VM, then SSH in and run ./setup.sh
# Usage:
#   .\push-oracle.ps1 -VmIp 203.0.113.10 -KeyPath "C:\Users\DAVID\.ssh\oracle-signaldesk.key"
param(
  [Parameter(Mandatory = $true)]
  [string] $VmIp,
  [Parameter(Mandatory = $true)]
  [string] $KeyPath,
  [string] $User = "ubuntu"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$KeyPath = Resolve-Path $KeyPath

$files = @(
  "xauusd_trader.py",
  "dashboard.html",
  "deploy\setup.sh",
  "deploy\signaldesk.service"
)
foreach ($f in $files) {
  $p = Join-Path $Root $f
  if (-not (Test-Path $p)) { throw "Missing $p" }
}

Write-Host "Uploading app to ${User}@${VmIp} ..."
foreach ($f in $files) {
  $name = Split-Path $f -Leaf
  scp -i $KeyPath -o StrictHostKeyChecking=accept-new (Join-Path $Root $f) "${User}@${VmIp}:~/$name"
}
Get-ChildItem (Join-Path $Root "predictions*.json") -ErrorAction SilentlyContinue | ForEach-Object {
  scp -i $KeyPath $_.FullName "${User}@${VmIp}:~/"
}
$tv = Join-Path $Root "tv_auth.json"
if (Test-Path $tv) { scp -i $KeyPath $tv "${User}@${VmIp}:~/" }

Write-Host ""
Write-Host "Next (SSH into the VM):"
Write-Host "  ssh -i `"$KeyPath`" ${User}@${VmIp}"
Write-Host "  chmod +x setup.sh && ./setup.sh"
Write-Host "  sudo tailscale up"
