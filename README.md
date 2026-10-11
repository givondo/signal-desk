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

A desk-wide risk governor sits above every instrument: a daily stop in R, a cap on open tickets, and A-grade only after a losing streak. When the daily stop is hit the whole desk shows HALT until the next EAT trading day.

## API

| Route | Purpose |
|---|---|
| `/api/signal?sym=XAUUSD` | full state for one instrument |
| `/api/roster` | every instrument plus the governor |
| `/api/desk/brief?sym=XAUUSD` | Precifarm analyst brief (plain text; `format=json`) |
| `/api/ask?sym=..&q=..` | rule-based copilot from live state |
| `/api/health` | unauthenticated health probe |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SIGNALDESK_PASS` / `SIGNALDESK_USER` | unset / `trader` | HTTP Basic auth on every route except health |
| `DATA_DIR` | app dir | where journals and state persist |
| `DESK_DAILY_STOP_R` | `4` | desk halts at this daily loss in R |
| `DESK_MAX_OPEN` | `4` | maximum open or working tickets across the desk |
| `DESK_LOSS_STREAK_TIGHTEN` | `2` | consecutive losses before A-grade only |
| `DESK_TZ` | `Africa/Nairobi` | trading-day boundary |
| `DESK_ACCOUNT_BALANCE`, `DESK_RISK_PER_TRADE_PCT` | `0`, `0.5` | lot sizing in the analyst brief |

Deployment guides live in `deploy/`. Analyst protocol: `docs/PRECIFARM_DESK_ANALYST.md`.

Educational and decision-support tool. Not financial advice.
