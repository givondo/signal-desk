# TradingView sync and MCP

Trades you place through your broker in TradingView flow into the Signal Desk tracker on their own.

```
TradingView Desktop (Trading Panel, your broker)
   │  read-only, 127.0.0.1:9222
   ▼
tools/tv_trade_sync.mjs  ──POST──▶  Signal Desk /api/tracker/import/fills  ──▶  Journal inbox
   ▲                                                                            │ add stop + note
tools/tv_mcp_server.mjs (Claude Desktop MCP)                                     ▼
                                                                          Trade journal
```

## What is and isn't touched
- **TradingView:** read only. The sync reads the text of the Account Manager tables. It never clicks, types, places, modifies or cancels an order.
- **Debug port:** listens on 127.0.0.1 only (this computer). Close TradingView to close it. Open TradingView with the launcher only on your own computer.
- **Tracker:** closed trades land in an inbox. A trade enters the journal only once its stop and money at risk are known. Duplicates are skipped by broker trade ID.

## Daily use
1. Open TradingView with the **TradingView Web (sync)** shortcut (`tools/start_tradingview_web.ps1`). This opens tradingview.com in its own Chrome or Edge window with the debug port, and works on the free Basic plan. Sign in and connect Deriv once in that window; the profile is kept in `%LOCALAPPDATA%\SignalDesk\tradingview-browser`.
   On a paid plan you can use TradingView Desktop instead: **TradingView (sync)** shortcut or `tools/start_tradingview_sync.ps1`. On Basic, the Desktop app shows an upgrade screen with no close button; the web window is the supported route, and the sync reads both the same way.
2. Keep a chart tab open with the Trading Panel's **History** tab visible.
3. The background sync (scheduled task *Signal Desk TradingView Sync*) posts new closed trades every 60 s.
4. In the tracker, open **Journal → From TradingView**, add the stop and a note, and click **File in journal**.

## Commands
```powershell
node tools/tv_trade_sync.mjs --dump       # show what the panel shows and how it maps
node tools/tv_trade_sync.mjs --once       # sync now
node tools/tv_trade_sync.mjs --watch 60   # keep syncing
node --test tools/tv_trade_sync.test.mjs  # mapping tests
```
Settings: `DESK_URL` (default http://localhost:8899), `TV_ACCOUNT_LABEL`, `TV_ACCOUNT_TYPE` (PERSONAL, DEMO or PROP), `TV_BROKER`. `DESK_USER` / `DESK_PASS` only for the password-protected live site; set them as your own Windows user variables, never in this repo.

### Paper Trading
TradingView's Paper Trading lists finished trades on the **Trade history** tab (under the panel's ••• menu), one row per trade with exit and entry stacked. Leave the panel on that tab. Run the sync with:

```
TV_BROKER="Paper Trading"  TV_ACCOUNT_TYPE=DEMO  TV_ACCOUNT_LABEL=TV-PAPER  TV_TZ_OFFSET_MIN=0
```

`TV_TZ_OFFSET_MIN` is the chart's time zone offset from UTC in minutes (shown bottom right of the chart; UTC is 0, Nairobi 180). Paper Trading shows net P&L only, so fees are not split out.

## Claude Desktop MCP
`tools/tv_mcp_server.mjs` gives Claude Desktop five tools: `tv_status`, `tv_read_closed_trades`, `tv_sync_now`, `desk_summary` and `desk_pending_trades`. Nothing on TradingView is changed.

TradingView's own MCP server (`https://mcp.tradingview.com/mcp`, Settings → Connectors → Add custom connector) adds market data, watchlists and alerts. It needs the Essential plan or higher, and it does not expose trades.

## If TradingView looks stretched or tiny
TradingView keeps its own page zoom. If charts look zoomed out, close TradingView, then reset the saved zoom: in `%LOCALAPPDATA%\Packages\TradingView.Desktop_*\LocalCache\Roaming\TradingView\Preferences`, remove the `www.tradingview.com` entry under `partition.per_host_zoom_levels` (back the file up first). The launcher also maximises the window so it fits the screen.

## Limits
- The reader uses TradingView Desktop's page structure, which TradingView can change in an update. If the sync reads zero trades after an update, run `--dump` and adjust the column names in `COLS`.
- Times are read as shown in the panel and converted using this computer's time zone. Keep TradingView's time zone set to Nairobi (or exchange time) consistently.
- Some brokers report realised P&L before fees, others after. Check one trade against your broker statement. If the P&L already includes fees, set the commission to 0 when filing.

## MCP servers for Claude (install_mcps.ps1)

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_mcps.ps1                       # desk on this PC
powershell -ExecutionPolicy Bypass -File tools\install_mcps.ps1 -DeskUrl https://desktrading-today.precifarm.com
```

The script installs Node.js and uv if missing (winget), caches `tradingview-mcp-server==0.8.1`, tests this repo's MCP server, backs up and updates `claude_desktop_config.json` (standard and Microsoft Store locations), and writes it without a byte-order mark. Quit Claude from the system tray and reopen it afterwards.

| Server | Tools you'll use most | Writes |
|---|---|---|
| signal-desk-tradingview | `desk_route_signal`, `desk_route_idea`, `desk_router_accounts`, `desk_summary`, `tv_read_closed_trades`, `tv_sync_now` | router log and tracker inbox on your own desk only |
| tradingview-mcp | `multi_timeframe_analysis`, `combined_analysis`, `backtest_strategy`, `walk_forward_backtest_strategy`, `financial_news`, `market_snapshot` | nothing |

Checked before adding (15 Nov 2026): `tradingview-mcp-server` 0.8.1 exposes no order tools (its only trade function is a local SQLite paper simulator that no MCP tool calls), needs no TradingView login, and reads public TradingView scanner, Yahoo Finance and news feeds. Its optional proxy and Marketaux news token stay off unless you set them.

Not added: TradingView Desktop MCP servers driven over the debug port (for example tradesdontlie/tradingview-mcp). They need the Desktop app, which asks for a paid plan, and their general `ui_click` and `ui_evaluate` tools can reach the order panel, which D-011 rules out.
