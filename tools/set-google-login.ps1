<#
  Switch on "Sign in with Google" for Northchart, on this laptop and on the GCP server.

  Google retired the command-line way to create OAuth clients (the IAP OAuth Admin API
  shut down on 19 March 2026), so two screens stay in the Cloud console. This script
  opens them at the right page, then does the rest:

    1. Opens the consent screen (Branding) and the "Create OAuth client" page.
    2. Asks for the client ID and secret (the secret is typed hidden).
    3. Saves them as your Windows user variables and restarts the laptop desk.
    4. Writes them into /etc/signaldesk.env on the server and restarts it. It also adds
       NORTHCHART_SECRET_KEY and NORTHCHART_PUBLIC_URL there if they are missing.
       The values travel in a short-lived file, never on a command line.

  Run:  powershell -ExecutionPolicy Bypass -File tools\set-google-login.ps1
  Or, with the client JSON downloaded from the console (no typing):
        powershell -ExecutionPolicy Bypass -File tools\set-google-login.ps1 -JsonPath "$HOME\Downloads\client_secret_....json"
#>
param(
  [string]$Project = "skilled-orbit-460722-h9",
  [string]$Instance = "signaldesk",
  [string]$Zone = "us-central1-a",
  [string]$PublicUrl = "https://desktrading-today.precifarm.com",
  [string]$DeskDir = (Split-Path -Parent $PSScriptRoot),
  [string]$JsonPath = "",
  [switch]$SkipConsole,
  [switch]$SkipServer
)
$ErrorActionPreference = "Stop"

function Step($n, $text) { Write-Host ""; Write-Host "[$n] $text" -ForegroundColor Cyan }

if ($JsonPath) { $SkipConsole = $true }
if (-not $SkipConsole) {
  Step 1 "Consent screen"
  Write-Host "  Opening Google Auth Platform > Branding for $Project."
  Write-Host "  If it asks you to get started: App name 'Northchart', support email david.givondoh@gmail.com,"
  Write-Host "  Audience 'External', contact email the same, accept the policy, Create."
  Write-Host "  Then Audience: leave it in Testing for now and add your Gmail as a test user, or press Publish app."
  Start-Process "https://console.cloud.google.com/auth/branding?project=$Project"
  Read-Host "  Press Enter when the consent screen is saved"

  Step 2 "OAuth client"
  Write-Host "  Opening Clients > Create client. Choose:"
  Write-Host "    Application type: Web application     Name: Northchart web"
  Write-Host "    Authorised redirect URIs (add both):"
  Write-Host "      $PublicUrl/auth/google/callback" -ForegroundColor Yellow
  Write-Host "      http://localhost:8899/auth/google/callback" -ForegroundColor Yellow
  Write-Host "  Press Create and keep the window with the client ID and secret open."
  Start-Process "https://console.cloud.google.com/auth/clients/create?project=$Project"
}

if ($JsonPath) {
  Step 3 "Reading the client file"
  $j = Get-Content -Raw $JsonPath | ConvertFrom-Json
  $c = if ($j.web) { $j.web } else { $j.installed }
  if (-not $c) { throw "That file is not a Google OAuth client file." }
  $id, $secret = "$($c.client_id)".Trim(), "$($c.client_secret)".Trim()
  if ($j.web -and $c.redirect_uris -notcontains "$PublicUrl/auth/google/callback") {
    Write-Host "  Note: the client file does not list $PublicUrl/auth/google/callback as a redirect URI." -ForegroundColor Yellow
  }
  if ($id -notmatch '^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$') { throw "No valid client ID in that file." }
} else {
  Step 3 "Paste the values"
  do {
    $id = (Read-Host "  Client ID (ends with .apps.googleusercontent.com)").Trim()
  } until ($id -match '^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$')
  $sec = Read-Host "  Client secret (hidden)" -AsSecureString
  $secret = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
  $secret = $secret.Trim()
}
if ($secret -notmatch '^[A-Za-z0-9_\-]{20,}$') { throw "That does not look like a Google client secret (it usually starts with GOCSPX-)." }

Step 4 "Laptop"
[Environment]::SetEnvironmentVariable("GOOGLE_CLIENT_ID", $id, "User")
[Environment]::SetEnvironmentVariable("GOOGLE_CLIENT_SECRET", $secret, "User")
$env:GOOGLE_CLIENT_ID = $id
$env:GOOGLE_CLIENT_SECRET = $secret
Write-Host "  Saved as Windows user variables GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
$port = Get-NetTCPConnection -LocalPort 8899 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($port) { Stop-Process -Id $port.OwningProcess -Force; Start-Sleep 2 }
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
Start-Process -FilePath $py -ArgumentList "xauusd_trader.py" -WorkingDirectory $DeskDir -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $DeskDir "local-run.log") -RedirectStandardError (Join-Path $DeskDir "local-run.err")
Start-Sleep 10
try {
  $state = Invoke-RestMethod http://localhost:8899/api/auth/state
  Write-Host ("  Laptop desk restarted. Google sign-in: " + $(if ($state.google) { "on" } else { "OFF (check the values)" }))
} catch { Write-Host "  Laptop desk did not answer yet; check local-run.err." -ForegroundColor Yellow }

if (-not $SkipServer) {
  Step 5 "Server ($Instance)"
  $q = { param($v) "'" + ($v -replace "'", "'\''") + "'" }
  $remote = @"
set -e
F=/etc/signaldesk.env
touch "`$F"; chmod 600 "`$F"
sed -i '/^GOOGLE_CLIENT_ID=/d;/^GOOGLE_CLIENT_SECRET=/d' "`$F"
printf 'GOOGLE_CLIENT_ID=%s\nGOOGLE_CLIENT_SECRET=%s\n' $(& $q $id) $(& $q $secret) >> "`$F"
grep -q '^NORTHCHART_SECRET_KEY=' "`$F" || echo "NORTHCHART_SECRET_KEY=`$(head -c 32 /dev/urandom | base64 | tr -d '=+/\n')" >> "`$F"
grep -q '^NORTHCHART_PUBLIC_URL=' "`$F" || echo 'NORTHCHART_PUBLIC_URL=$PublicUrl' >> "`$F"
systemctl restart signaldesk
sleep 2
echo "service: `$(systemctl is-active signaldesk)"
echo "settings present: `$(grep -o '^[A-Z_]*=' "`$F" | tr -d = | tr '\n' ' ')"
"@
  # gcloud on Windows answers plink's prompts through stdin, so the settings go over as a
  # short-lived file in your home folder on the VM, run once and deleted on both sides.
  $gc = (Get-Command gcloud.cmd -ErrorAction Stop).Source
  $tmp = Join-Path $env:TEMP ("nc-google-" + [guid]::NewGuid().ToString("N").Substring(0, 8) + ".sh")
  [IO.File]::WriteAllText($tmp, ($remote -replace "`r", ""), (New-Object Text.UTF8Encoding($false)))
  icacls $tmp /inheritance:r /grant:r "${env:USERNAME}:F" | Out-Null
  $name = Split-Path $tmp -Leaf
  try {
    & $gc compute scp $tmp "${Instance}:$name" --zone=$Zone --project=$Project
    if ($LASTEXITCODE -ne 0) { throw "Could not copy the settings to the VM." }
    & $gc compute ssh $Instance --zone=$Zone --project=$Project --command="sudo bash ~/$name; rm -f ~/$name"
  } finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
  }
  if ($LASTEXITCODE -ne 0) { throw "The server step failed. Run again with -SkipConsole once gcloud can reach the VM." }
}

$secret = $null
Write-Host ""
Write-Host "Done. Try it: http://localhost:8899/signin > Continue with Google." -ForegroundColor Green
Write-Host "The live site shows the button once PR #5 is merged and deployed."
