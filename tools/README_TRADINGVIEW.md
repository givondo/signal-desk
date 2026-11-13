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
1. Open TradingView with the **TradingView (sync)** desktop shortcut (or `tools/start_tradingview_sync.ps1`).
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

## Claude Desktop MCP
`tools/tv_mcp_server.mjs` gives Claude Desktop five tools: `tv_status`, `tv_read_closed_trades`, `tv_sync_now`, `desk_summary` and `desk_pending_trades`. Nothing on TradingView is changed.

TradingView's own MCP server (`https://mcp.tradingview.com/mcp`, Settings → Connectors → Add custom connector) adds market data, watchlists and alerts. It needs the Essential plan or higher, and it does not expose trades.

## Limits
- The reader uses TradingView Desktop's page structure, which TradingView can change in an update. If the sync reads zero trades after an update, run `--dump` and adjust the column names in `COLS`.
- Times are read as shown in the panel and converted using this computer's time zone. Keep TradingView's time zone set to Nairobi (or exchange time) consistently.
- Some brokers report realised P&L before fees, others after. Check one trade against your broker statement. If the P&L already includes fees, set the commission to 0 when filing.
