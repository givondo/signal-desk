# Signal Desk

Multi-asset confluence terminal for discretionary-systematic trading. XAU/USD first; metals, crypto, FX majors, indices and WTI also covered. Stdlib Python backend, single-file Vue dashboard.

```
python xauusd_trader.py          # http://localhost:8899
python -m unittest discover -s tests
```

## What the desk does

Every 15 seconds the engine pulls multi-timeframe technicals from the TradingView scanner, the macro complex (DXY, yields, VIX and more) and 15m structure, and scores a composite bias per instrument. A signal is only marked tradeable when regime, multi-timeframe agreement, daily bias, macro, session liquidity and extension filters all agree.

Tradeable signals are paper-executed and journaled so the statistics are honest:

- limit entries wait for a real fill and expire if they are not filled
- 50% is banked at TP1, the runner stop moves to entry, the runner targets TP2
- gapped stops are booked at the actual price; estimated spread is charged on every trade
- MFE and MAE are recorded per ticket

A desk-wide risk governor sits above every instrument: a daily stop in R, a cap on open tickets, one open ticket per correlated group, and A-grade only after a losing streak. Correlated groups are crypto, metals, indices and energy, with FX majors grouped by the direction they put on the US dollar (short EURUSD and long USDJPY are both long USD), so three crypto shorts cannot fill the desk as if they were three separate bets. When the daily stop is hit the whole desk shows HALT until the next EAT trading day.

## Trading tracker and Trading OS

`/tracker` (also linked as **Tracker** in the desk header) is the record-keeping side of the desk, built on the GIVONDO Trading OS in `trading_os/`:

- **Journal:** live, prop, demo and paper trades; missed trades; rule violations; corrections (rows are append-only and sealed, so a trade cannot be rewritten later).
- **Paper sync:** closed desk paper tickets are copied into the journal automatically as PAPER trades in R, so engine statistics outlive the 500-ticket buffer.
- **Prop firms:** purchases, resets, payouts and per-session account snapshots, checked against each firm's drawdown rules (daily floor, internal stop, maximum floor, OK / WARN / CRITICAL / BREACHED).
- **Capital:** broker accounts, deposits, withdrawals (KES and original currency) and monthly reconciliation, with trading returns separated from deposits.
- **Calculators:** position size (rounds down, includes spread, slippage and commission, refuses risk above the hard cap) and prop drawdown check.
- **Exports:** CSV per record type and a full zip backup.

Records live in `DATA_DIR/trading_os_data/` (set `TOS_DATA_DIR` to override). That folder is git-ignored: this repository is public, so personal figures must never be committed. Back it up from the Overview tab.

The Trading OS documents (prop firm research and scores, risk policy, strategy specs, review templates) are in `trading_os/`; start at `trading_os/00_README.md`. Its calculators and analytics have their own tests: `cd trading_os && python -m pytest -q`.

## TradingView trade sync

Closed trades from TradingView's trading panel (Paper Trading or a connected broker) flow into the tracker's journal inbox every 60 seconds. The reader is read-only: it never places, changes or cancels anything. On TradingView's free plan use `tools/start_tradingview_web.ps1` (the Desktop app asks for a paid plan); keep the panel on **Trade history** for Paper Trading. A trade moves from the inbox into the journal once its stop and money at risk are added. The desk header shows how many are waiting. Setup and settings: `tools/README_TRADINGVIEW.md`.

## API

| Route | Purpose |
|---|---|
| `/api/signal?sym=XAUUSD` | full state for one instrument |
| `/api/roster` | every instrument plus the governor |
| `/api/desk/brief?sym=XAUUSD` | Precifarm analyst brief (plain text; `format=json`) |
| `/api/ask?sym=..&q=..` | rule-based copilot from live state |
| `/api/health` | unauthenticated health probe |
| `/tracker` | trading tracker page |
| `/api/tracker/summary` | journal metrics, prop status, prop economics, capital (syncs paper tickets) |
| `/api/tracker/records?kind=` / `export?kind=` / `backup` | read rows, CSV export, zip backup |
| `/api/tracker/append` (POST) | append one validated row `{kind, row}` |
| `/api/tracker/size`, `/api/tracker/drawdown` (POST) | position size and prop drawdown calculators |
| `/api/tracker/import/fills` (POST), `/api/tracker/fills/pending` | closed broker or TradingView trades into the journal inbox |
| `/api/tracker/workbook.xlsx` | Excel workbook with live formulas, including net worth |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SIGNALDESK_PASS` / `SIGNALDESK_USER` | unset / `trader` | HTTP Basic auth on every route except health |
| `DATA_DIR` | app dir | where journals and state persist |
| `DESK_DAILY_STOP_R` | `4` | desk halts at this daily loss in R |
| `DESK_MAX_OPEN` | `4` | maximum open or working tickets across the desk |
| `DESK_MAX_PER_CLUSTER` | `1` | maximum open tickets per correlated group |
| `DESK_LOSS_STREAK_TIGHTEN` | `2` | consecutive losses before A-grade only |
| `DESK_TZ` | `Africa/Nairobi` | trading-day boundary |
| `DESK_ACCOUNT_BALANCE`, `DESK_RISK_PER_TRADE_PCT` | `0`, `0.5` | lot sizing in the analyst brief |

Deployment guides live in `deploy/`. Analyst protocol: `docs/PRECIFARM_DESK_ANALYST.md`.

Educational and decision-support tool. Not financial advice.
