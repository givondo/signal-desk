# Opens TradingView (web, free on the Basic plan) in its own Chrome or Edge window
# with the local debug port that tv_trade_sync.mjs reads. Use this instead of
# TradingView Desktop when the Desktop app asks for a paid plan.
#
# The window uses a dedicated browser profile (your normal Chrome profile is not
# touched), so sign in to TradingView and connect Deriv once in this window.
# The debug port listens on 127.0.0.1 only.

param(
  [int]$Port = 9222,
  [string]$Url = "https://www.tradingview.com/chart/"
)

$ErrorActionPreference = "Stop"

# TradingView Desktop also uses this port; close it so the two do not clash.
$desk = Get-Process -Name "TradingView" -ErrorAction SilentlyContinue
if ($desk) {
  Write-Host "Closing TradingView Desktop so the web window can use port $Port..."
  $desk | ForEach-Object { $_.CloseMainWindow() | Out-Null }
  Start-Sleep -Seconds 3
  Get-Process -Name "TradingView" -ErrorAction SilentlyContinue | Stop-Process -Force
}

# Already running with the port open? Nothing to do.
try {
  $tabs = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/list" -TimeoutSec 2
  if ($tabs | Where-Object { $_.url -like "*tradingview.com*" }) {
    Write-Host "TradingView web window is already open on port $Port."
    exit 0
  }
} catch { }

$candidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
)
$browser = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw "Neither Chrome nor Edge was found." }

$profileDir = Join-Path $env:LOCALAPPDATA "SignalDesk\tradingview-browser"
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

$args = @(
  "--remote-debugging-port=$Port",
  "--remote-debugging-address=127.0.0.1",
  "--user-data-dir=`"$profileDir`"",
  "--no-first-run",
  "--no-default-browser-check",
  "--start-maximized",
  "--app=$Url"
)
Start-Process -FilePath $browser -ArgumentList $args
Write-Host "Opened TradingView in $(Split-Path $browser -Leaf) with debug port $Port."
Write-Host "First time only: sign in to TradingView in this window, then connect Deriv from the Trading Panel."
