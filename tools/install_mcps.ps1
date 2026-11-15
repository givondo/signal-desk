<#
Install the TradingView MCP servers for Claude on this Windows PC.

  signal-desk-tradingview  this repo's server: TradingView Trading Panel (read-only), tracker,
                           and the live/prop router (desk_route_signal, desk_route_idea)
  tradingview-mcp          atilaahmettaner/tradingview-mcp, PyPI tradingview-mcp-server 0.8.1 (MIT):
                           technical analysis, screeners, multi-timeframe, backtests, news.
                           No TradingView login, no order tools.

Neither server can place, modify or cancel an order (decision D-011).

Run from the repo folder:
  powershell -ExecutionPolicy Bypass -File tools\install_mcps.ps1
  powershell -ExecutionPolicy Bypass -File tools\install_mcps.ps1 -DeskUrl https://desktrading-today.precifarm.com

For the password-protected live site, set DESK_USER and DESK_PASS as your own Windows user
variables (never in a file in this repo):
  [Environment]::SetEnvironmentVariable("DESK_USER","trader","User")
  [Environment]::SetEnvironmentVariable("DESK_PASS","<your desk password>","User")
Then quit Claude fully (system tray > Quit) and reopen it.
#>
param(
  [string]$DeskUrl = "http://localhost:8899",
  [string]$TvMcpVersion = "0.8.1"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$server = Join-Path $repo "tools\tv_mcp_server.mjs"

function Step($m) { Write-Host ">> $m" -ForegroundColor Green }

# 1. Node 18+ for the Signal Desk server
Step "Checking Node.js"
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
  Step "Installing Node.js LTS with winget"
  winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
}
$major = [int]((node --version).TrimStart("v").Split(".")[0])
if ($major -lt 18) { throw "Node $major found; version 18 or newer is needed (winget upgrade OpenJS.NodeJS.LTS)" }

# 2. uv for the Python TradingView MCP
Step "Checking uv"
if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
  Step "Installing uv with winget"
  winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
}
Step "Downloading tradingview-mcp-server $TvMcpVersion (cached for uvx)"
uv tool install "tradingview-mcp-server==$TvMcpVersion" --force | Out-Host

# 3. Quick self-test of the Signal Desk MCP server
Step "Testing the Signal Desk MCP server"
$probe = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' + "`n" + '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' + "`n"
$out = $probe | node $server 2>$null | Select-Object -First 2
if (-not ($out -join "" | Select-String "desk_route_signal")) { throw "Signal Desk MCP server did not list its tools" }

# 4. Register both servers with Claude Desktop (every config location that exists, plus the standard one)
$entryDesk = [ordered]@{ command = "node"; args = @($server.Replace("\","/")); env = [ordered]@{ DESK_URL = $DeskUrl } }
$entryTv = [ordered]@{ command = "uvx"; args = @("--from", "tradingview-mcp-server==$TvMcpVersion", "tradingview-mcp") }
$targets = @(Join-Path $env:APPDATA "Claude\claude_desktop_config.json")
$pkg = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Packages") -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue
foreach ($p in $pkg) { $targets += (Join-Path $p.FullName "LocalCache\Roaming\Claude\claude_desktop_config.json") }

foreach ($cfg in ($targets | Select-Object -Unique)) {
  $dir = Split-Path -Parent $cfg
  if (-not (Test-Path $dir)) { if ($cfg -ne $targets[0]) { continue }; New-Item -ItemType Directory -Path $dir | Out-Null }
  $json = [pscustomobject]@{}
  if (Test-Path $cfg) {
    Copy-Item $cfg "$cfg.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
    $raw = Get-Content $cfg -Raw
    if ($raw.Trim()) { $json = $raw | ConvertFrom-Json }
  }
  if (-not $json.mcpServers) { $json | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{}) -Force }
  $json.mcpServers | Add-Member -NotePropertyName "signal-desk-tradingview" -NotePropertyValue $entryDesk -Force
  $json.mcpServers | Add-Member -NotePropertyName "tradingview-mcp" -NotePropertyValue $entryTv -Force
  # UTF-8 without a byte-order mark: Claude Desktop cannot parse a config that starts with one
  [IO.File]::WriteAllText($cfg, ($json | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding $false))
  Step "Registered both servers in $cfg (backup kept beside it)"
}

Write-Host ""
Write-Host "Done. Quit Claude fully (system tray > Quit) and reopen it." -ForegroundColor Green
Write-Host "Try: 'Route the desk's XAUUSD signal across my accounts' or"
Write-Host "     'multi_timeframe_analysis for XAUUSD on OANDA'."
Write-Host "Claude Code in this folder picks the same servers up from .mcp.json."
