# TradingView teardown and open-source survey

What Northchart should copy, what it should do differently, and what it must stop depending on. Written 21 November 2026.

Scope: this is a teardown of TradingView's public product (its website, documentation and published library licences) and of open-source projects on GitHub. We did not decompile, scrape or copy any TradingView code, and we should not: TradingView's charting libraries are proprietary, and using their private endpoints to build a competitor is a terms-of-service and business risk.

## 1. How TradingView wins

| Layer | What TradingView does | Why it matters |
|---|---|---|
| Chart engine | Fast browser charts, 100+ indicators, 80+ drawing tools, multi-chart layouts saved to the cloud | The chart is the product; everything else hangs off it |
| Pine Script | Its own scripting language for indicators and strategies, with a strategy tester | Users build on the platform, so they cannot leave easily |
| Alerts | Server-side alerts on price, indicators and Pine conditions | Works while the user is away; a paid-tier driver |
| Community | Published ideas, public scripts, follow graph | Free content and a network effect no competitor can buy |
| Broker panel | Trade from the chart through many connected brokers | Keeps execution on the platform |
| Distribution | Free charting libraries and widgets on broker and media sites, each carrying TradingView's attribution | Every embed advertises TradingView |
| Pricing | Free tier, then paid tiers up to Premium at $59.95/month and Ultimate at $99.95/month | High price for active traders, very high for most African traders |

The distribution layer is the lesson. TradingView gives its libraries away with an attribution requirement (Lightweight Charts) or a public-and-free-only licence (Advanced Charts), so its brand rides on other people's products.

## 2. Where Northchart can win

TradingView is a general tool for everyone. Northchart should win a narrow position first:

1. **Agent-native.** Every capability is a tool that ChatGPT, Claude or any MCP client can call (Northchart Connect), and the web UI uses the same tools in its agent desk. TradingView's AI story is bolted on through third-party bridges that automate its desktop app.
2. **Risk-first.** Signals are graded and gated by a risk governor, sized per account by the router, and journalled in a sealed record. TradingView shows indicators; Northchart shows whether a trade is allowed.
3. **Priced and paid for Kenya and Africa.** M-Pesa, KES pricing, local session clock (EAT), CMA-aware positioning.
4. **A live floor.** The physical trading floor and the daily show (The Desk Today) give community content TradingView's ideas feed cannot copy locally.

## 3. Open-source projects worth using or learning from

| Project | Licence | Use for Northchart |
|---|---|---|
| KLineChart and KLineChart Pro (klinecharts) | Apache 2.0 | **Recommended chart engine.** Canvas, zero dependencies, mobile support, indicators and drawing overlays, no on-chart vendor logo |
| TradingView Lightweight Charts | Apache 2.0 with mandatory TradingView attribution | Fine for quick widgets; wrong for a competitor because every chart credits TradingView |
| LuxAlgo Vela | Apache 2.0, small attribution mark removed by any paid licence | Second option if KLineChart falls short |
| OpenCharts (self-hosted TradingView alternative) | Open source | Reference architecture: WebSocket streaming, alerts, plugin system, Pine-like transpiler |
| OpenAlgo | Open source | Reference for broker adapters and order-routing safety, when execution is ever authorised |
| tradingview-mcp (atilaahmettaner) | MIT | Already used for research; keep it out of the production data path |

## 4. What we must stop depending on

The engine reads technicals from `scanner.tradingview.com`, an undocumented TradingView endpoint, plus Yahoo Finance's unofficial chart API. The dashboard chart is TradingView's hosted widget (`s3.tradingview.com/tv.js`). For a product that competes with TradingView these are the top risks: either source can be cut off or enforced against without notice.

Replace in this order:

1. **Licensed price data** for metals and FX first (XAUUSD is the core instrument), then indices, crypto and oil. Compute every indicator in our own engine from raw OHLC, which the backtester already does.
2. **Own chart engine** (KLineChart) fed by our own data, replacing the hosted widget.
3. Retire the TradingView session login in the header once the engine no longer needs it.

## 5. Adopt list, in order

| # | Item | Status |
|---|---|---|
| 1 | Agent desk in the UI (command bar, result cards, activity feed, connect guide), opened with Ctrl+K or `/` like a command palette | Done in this change |
| 2 | Northchart Connect remote MCP server with OAuth sign-in for ChatGPT and Claude, read-only tools | Done in this change |
| 3 | Northchart naming in the UI | Done in this change |
| 4 | Licensed data feed and own indicator pipeline | Next: needs a vendor decision and budget |
| 5 | KLineChart chart engine with saved layouts and drawing sync | Next |
| 6 | Server-side alerts that also notify agents (MCP) and Telegram | Next |
| 7 | Strategy lab in the UI, exposing the existing backtester and validation gate | Next |
| 8 | Public read-only tier of Connect (no sign-in) for market overview and instruments, as a growth channel | Later, after data licensing allows redistribution |
| 9 | Order execution through agents | Not planned: decision D-011 stands, and Stage 2 regulatory review comes first |
