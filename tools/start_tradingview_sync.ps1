# Start TradingView Desktop so the Signal Desk trade sync can read the Trading Panel.
# The debug port listens on 127.0.0.1 only (this computer). The sync never places,
# changes or cancels orders; it only reads the Account Manager tables.
$ErrorActionPreference = "Stop"
$pkg = Get-AppxPackage *TradingView* | Select-Object -First 1
if (-not $pkg) { Write-Error "TradingView Desktop is not installed."; exit 1 }
$exe = Join-Path $pkg.InstallLocation "TradingView.exe"
$running = Get-CimInstance Win32_Process -Filter "Name='TradingView.exe'" | Select-Object -First 1
if ($running -and $running.CommandLine -notmatch "remote-debugging-port") {
    Write-Host "TradingView is open without sync access. Closing it so it can reopen with sync..."
    Get-Process TradingView -ErrorAction SilentlyContinue | Stop-Process
    Start-Sleep -Seconds 3
}
if (-not (Get-CimInstance Win32_Process -Filter "Name='TradingView.exe'")) {
    Start-Process -FilePath $exe -ArgumentList "--remote-debugging-port=9222", "--remote-debugging-address=127.0.0.1"
}
# Fit the window to the screen: TradingView can restore a size taller than the display.
Add-Type @"
using System; using System.Runtime.InteropServices;
public class TvWin { [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c); }
"@
for ($i = 0; $i -lt 30; $i++) {
    $w = Get-Process TradingView -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    if ($w) { [TvWin]::ShowWindow($w.MainWindowHandle, 3) | Out-Null; break }
    Start-Sleep -Milliseconds 500
}
Write-Host "TradingView started with sync access on 127.0.0.1:9222."
