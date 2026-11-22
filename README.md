# Northcharts

Multi-asset charts, confluence signals and an agent desk. XAU/USD first; metals, crypto, FX majors, indices and WTI also covered. Stdlib Python backend, single-file Vue dashboard. This repository was called Signal Desk; the code, service names and data paths keep the old names so existing deployments update in place.

| Name | What it is |
|---|---|
| Northcharts | The platform: dashboard, tracker and router |
| Northcharts Connect | The agent-facing interface: a remote MCP server for ChatGPT, Claude and other MCP clients |
| Agent desk | The panel in the dashboard (Ctrl+K or `/`) that runs the same tools and shows what agents are doing |

Strategy and competitive notes: `docs/TRADINGVIEW_TEARDOWN.md`.

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

## Journal (automatic)

`/journal` (**Journal** in the header) is a sheet of every trade you take, filled in by itself. Trades arrive from TradingView trade sync and broker imports (`/api/tracker/import/fills`). `northchart_journal.py` then:

- matches each trade to the desk's view when it opened (bias, grade, mode, regime, session) from a snapshot the engine keeps every five minutes, and labels it With desk, Against desk or Desk neutral;
- takes the stop from the broker, or, when you traded in the desk's direction near its entry, from the desk's stop at that moment (labelled as such);
- works out money at risk from the trade's own profit per point, so R multiples are exact without contract-size guesses;
- writes the trade into the sealed, append-only journal. A trade with no stop it can trust stays in the sheet as **Needs stop** until you type one in. Notes, emotion and later stop changes are stored as corrections, never overwrites.

Every stop, emotion and note carries its source, shown as a coloured edge on the field: blue for your **broker**, gold for the **desk**, green for **you**, grey italic for a **draft** not yet confirmed (✓ accepts it). Each cell stays on one line; "No stop used" sits in the small ▾ menu beside the stop. Your entry always wins and nothing automatic overwrites it. Emotion is never set automatically: behaviour flags (Revenge, FOMO, Overtrading, Against desk, Moved stop, Early exit, No stop) suggest one for a one-click confirm. Notes start as a draft paragraph built from what the desk recorded (setup, desk call, time held, best and worst R on the desk's 5-minute prices, whether the target was reached, one lesson); Claude rewrites it when `ANTHROPIC_API_KEY` is set. A trade with no stop can be marked **No stop used**, which keeps it out of R statistics and counts it as a discipline breach. **Review** walks through unreviewed trades one at a time (keys 1 to 8 pick an emotion, Enter saves); the header badge shows how many wait.

The strip above the sheet shows trades, win rate, total and average R, net P/L, the longest losing streak, how often you traded with or against the desk, and how many trades still need a stop. CSV and Excel exports are one click. Agents read it through the `get_journal` tool.

The Tracker and Router pages were removed in favour of the journal; `/tracker` and `/router` redirect here. Their APIs stay: TradingView sync posts fills to `/api/tracker/*`, and the agent's `preview_signal_routing` tool uses the router.

## Accounts

Sign in at `/signin`. The first account is the owner, created at `/setup` with the current desk password (`SIGNALDESK_PASS`), or from the computer running the desk when none is set. The owner keeps the existing journal and invites other traders from **Account**; sign-up is invite-only unless `NORTHCHART_SIGNUP=open` (or `closed`). Every member gets an empty journal of their own under `DATA_DIR/users/<id>/`; market data and desk signals are shared.

Passwords are stored as scrypt hashes; sessions are HttpOnly, SameSite=Lax cookies (Secure over HTTPS) and the server keeps only their SHA-256; every cookie-authenticated write must come from the same origin; five failed sign-ins lock an email or address for 15 minutes. Verification and reset emails go out over SMTP when `SMTP_HOST` is set, otherwise they are written to `DATA_DIR/accounts/outbox.log`. The account page covers profile, password, active sessions, sync tokens, invites, data export (zip of CSVs) and deleting the account. ChatGPT and Claude now sign in with a Northcharts email and password when connecting, and see only that user's journal; routing and portfolio tools stay owner-only.

**Sign in with Google.** Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` from a "Web application" OAuth client in Google Cloud whose authorised redirect URI is `<desk address>/auth/google/callback`. "Continue with Google" then appears on the sign-in and sign-up pages. It uses the authorisation code flow with PKCE, a state bound to the browser and a nonce; issuer, audience, expiry, nonce and verified email are checked. A Google address that matches an existing account connects to it; a new address needs an invite unless sign-up is open. Google-only accounts can set a password later on the account page, and any account can connect or disconnect Google there.

Scripts use a **sync token** (`Bearer nci_...`, made on the account page) that can only send trades, or the account email and password as HTTP Basic. The old desk password keeps working as the owner for one release; `NORTHCHART_LEGACY_BASIC=0` turns it off.

## Broker connections

**Account > Brokers and data**: search for your broker (CMA-licensed brokers are marked) and pick a route. All of them are read-only and feed the same journal pipeline.

- **Northcharts Bridge** (MetaTrader 4 and 5, free): `tools/northchart_bridge/NorthchartBridge.mq5` and `.mq4`, downloadable from the account page with the desk address filled in. It reads closed trades from the terminal's history and posts them to `/api/ingest/bridge` with a sync token. It contains no trading calls and the password never leaves the terminal. MT5 sends the stop at entry and the stop at the close, so a widened stop is flagged.
- **Investor password via MetaApi** (paid, owner's choice): set `NORTHCHART_METAAPI_TOKEN`. The trader enters account number, server and the investor password once; the desk creates the MetaApi account, refuses a master password using MetaApi's `investorMode` flag, keeps only the sealed MetaApi account id and pulls new trades every ten minutes.
- **Statement import**: MT5 and MT4 HTML reports, cTrader and prop-firm CSVs, and any CSV whose columns are detected by name or picked by hand. Duplicates are skipped.
- **TradingView trade sync**: `tools/start_tradingview_sync.ps1` with `DESK_TOKEN` set to a sync token.

Planned: cTrader Open API, Interactive Brokers Flex, Binance read-only keys; OANDA and the Deriv API wait for a read-only option. Secrets are sealed with an HMAC-SHA256 stream cipher plus tag, keyed by `NORTHCHART_SECRET_KEY` (set it in production) or a generated `DATA_DIR/accounts/secret.key`.

## Plans and billing

Desk calls stay free. Members pay for the tools (`northchart_billing.py`, pages in `northchart_billing_pages.py`):

| Plan | Price | Adds |
|---|---|---|
| Free | $0 | Desk calls, chart, manual journal, statement import, 3 agent questions a day |
| Plus | $25 a month | Automatic broker sync (2 accounts), Bridge and TradingView sync, agent desk (60 a day), Claude journal notes |
| Pro | $200 a month | 20 broker accounts, MetaApi, agent desk (1,000 a day), Northcharts Connect for ChatGPT and Claude |

* **M-Pesa** through Paystack by default (`MPESA_PROVIDER=paystack`): an STK push from the same Paystack account
  that takes cards, so one account, one dashboard and one payout. Every result is re-checked with
  `/transaction/verify`. Paystack cannot repeat an M-Pesa payment on its own, so renewal is a prompt on the due date
  (three tries, a day apart). Turn on Mobile Money in the Paystack dashboard (Settings, Preferences).
  With a Paybill or Till of your own, `MPESA_PROVIDER=daraja` sends the prompt through Safaricom Daraja instead:
  callbacks are unsigned, so each one only triggers an `stkpushquery`, and the query decides. Daraja also enables
  M-Pesa Ratiba standing orders; Ratiba debits arrive as C2B confirmations, accepted only from Safaricom's addresses
  for the member's account reference and the right amount. Plans are monthly only.
* **Cards** through Paystack's hosted page (no card data touches the desk). The first payment saves a reusable
  authorisation, sealed in the vault; renewals use `charge_authorization`. Webhooks are checked with HMAC-SHA512 and
  every success is re-verified with `/transaction/verify`.
* Prices are in USD and charged in KES at the day's USDKES rate (rounded up to KES 50), or a fixed KES price.
* Upgrades start at once with credit for unused time; downgrades wait for the renewal date.
  Three days' grace, then Free. The owner always has every feature and can give members a plan from `/billing`.

Switching it on (sandbox first):

1. Safaricom: create an app at developer.safaricom.co.ke with **M-Pesa Express** and **M-Pesa Ratiba**; set
   `DARAJA_CONSUMER_KEY` and `DARAJA_CONSUMER_SECRET`. The sandbox shortcode and passkey are filled in for you.
2. Paystack: sign up as a Kenyan business and copy the test secret key into `PAYSTACK_SECRET_KEY`. Set the webhook URL in
   the Paystack dashboard to `https://<desk>/api/billing/paystack/webhook`.
3. Set `NORTHCHART_BILLING=test`, `NORTHCHART_PUBLIC_URL` and `NORTHCHART_SIGNUP=open`, restart, and pay a plan from a
   member account. Then press **Register M-Pesa confirmation address** on `/billing` (needed for Ratiba).
4. Go live: Safaricom Go Live with your Paybill or Till (`DARAJA_ENV=production`, `DARAJA_SHORTCODE`, `DARAJA_PASSKEY`,
   and `DARAJA_TILL` for a Till), Paystack live key, `NORTHCHART_BILLING=live`.

| Variable | Meaning |
|---|---|
| `NORTHCHART_BILLING` | `off` (default, nothing locked), `test` or `live` |
| `NORTHCHART_PRICE_PLUS_MONTH`, `NORTHCHART_PRICE_PRO_MONTH` | USD monthly price; add `_KES` to fix the shilling price |
| `NORTHCHART_KES_PER_USD` | Fixed rate instead of the live USDKES quote |
| `PAYSTACK_SECRET_KEY`, `PAYSTACK_CURRENCY` | Card payments; currency `KES` (default) or `USD` once Paystack approves it |
| `DARAJA_ENV`, `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET` | M-Pesa API app (`sandbox` or `production`) |
| `DARAJA_SHORTCODE`, `DARAJA_PASSKEY`, `DARAJA_TILL` | Paybill shortcode and Lipa na M-Pesa passkey; Till number for Buy Goods |
| `NORTHCHART_LEGAL_NAME`, `NORTHCHART_KRA_PIN` | Shown on receipts |

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

## Live and prop router

`/router` (**Router** in the desk header) takes one signal, from the desk or a manual idea, and checks it against every account in the portfolio: TradingView Paper, the personal Deriv account and each prop account. Each account gets its own answer:

| Answer | Meaning |
|---|---|
| TICKET | every check passed and the account is ACTIVE: place it by hand at the size shown |
| WATCH | desk mode is PREPARE: levels staged, no entry yet |
| DRY_RUN | checks passed but the account is PLANNED or PAUSED: what the ticket would be |
| BLOCKED | a rule failed; the reasons list which |

Checks per account: symbol list, equity on record, losses in a row today, daily and weekly loss limits (personal), the firm's drawdown rules with the internal daily stop and risk cut at half the maximum loss (prop), and a position size that rounds down with spread and commission included. Limits come from `trading_os/06_Risk_Management/risk_limits.json` (decision D-008) and cap whatever an account asks for.

**The router never sends an order (decision D-011).** You place each ticket yourself and mark it Taken, Skipped or Missed, which keeps signal quality and execution separate; `/api/router/signals.csv` exports the log in the column order of the Trading OS Signals sheet.

Accounts live in `DATA_DIR/trading_os_data/router/accounts.json` on the server (private), created on first use from `trading_os/router_accounts.example.json`. Set balances on the Router page before each session; snapshots logged in the tracker take priority. Journal each trade under the same account label as the router (for example `FTMO-2STEP`), so its losses count towards that account's daily and consecutive-loss limits. Optional Telegram alerts for each new EXECUTE signal, with every account's size: put `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `/etc/signaldesk.env` on the server and restart the service.

## Trader research and the strategy lab

`trading_os/14_Trader_Research/traders.json` tracks traders whose records can be checked (audited World Cup results, certified platforms, published methods), what each actually does, and which setup we are testing because of them. Six setups from that research are coded as hypotheses in `trading_os/05_Backtesting/gtos_backtest/champions.py`: S-011 volatility breakout (Larry Williams), S-012 prior-day breakout (Andrea Unger), S-013 Turtle Soup and S-014 Holy Grail (Linda Raschke), S-015 NR7 with opening range breakout (Toby Crabel), S-016 Turtle System 2, plus the 200-day filter F-01 (Paul Tudor Jones). Kevin Davey's incubation and quit rules are part of the validation gate. None is traded until it passes the gate on real XAUUSD data. A weekly scheduled task refreshes the list in the Trading OS Sheet; `py tools/trader_research.py check` validates the file.

## Claude and TradingView MCP servers

`tools/install_mcps.ps1` sets up two MCP servers for Claude Desktop on Windows, and `.mcp.json` gives Claude Code the same two in this folder:

- `signal-desk-tradingview` (this repo): TradingView Trading Panel reading, tracker summary, and the router (`desk_route_signal`, `desk_route_idea`, `desk_router_accounts`).
- `tradingview-mcp` (atilaahmettaner/tradingview-mcp, PyPI `tradingview-mcp-server` 0.8.1, MIT): technical analysis, screeners, multi-timeframe alignment, backtests and news from public TradingView and Yahoo data. No TradingView login. Use `exchange="OANDA"` for XAUUSD and FX.

Neither can place, modify or cancel an order. MCP servers that drive TradingView Desktop through its debug port were left out: they need a paid plan for the Desktop app, and their general click tools can reach the order panel. Details: `tools/README_TRADINGVIEW.md`.

## Northcharts Connect (MCP for ChatGPT and Claude)

The server exposes a remote MCP endpoint at `/mcp` (Streamable HTTP, protocol versions 2025-11-25, 2025-06-18 and 2025-03-26). It is part of the same process as the dashboard, so agents and people read the same engine state.

**Tools (all read-only, `readOnlyHint: true`):** `list_instruments`, `get_market_overview`, `get_market_snapshot`, `get_signal_detail`, `get_price_history`, `get_desk_brief`, `ask_desk`, `preview_signal_routing` (nothing is logged, no order is placed) and `get_portfolio_summary`. Nothing in Connect can place, change or cancel an order, edit the journal or change an account (decision D-011).

**Sign-in.** Connect runs its own small OAuth 2.1 server: discovery at `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server`, Dynamic Client Registration at `/oauth/register`, PKCE S256 required, one-hour access tokens and 30-day refresh tokens rotated on every use, all stored hashed in `DATA_DIR/northchart_connect/oauth.json`. A client is approved on a Northcharts consent page with the desk password (`SIGNALDESK_PASS`, or `NORTHCHART_OWNER_PASS` if you want a separate one). Five wrong passwords lock the page for 15 minutes per address. Redirects are limited to Claude's and ChatGPT's callbacks and loopback addresses; add others with `NORTHCHART_REDIRECT_ALLOW` (comma-separated prefixes). Tokens are never accepted in a URL.

**Connect Claude:** Settings, Connectors, Add custom connector, paste `https://<your-host>/mcp`, approve with the desk password.

**Connect ChatGPT:** Settings, Apps, Create (developer mode must be available on the plan), paste the same URL, choose OAuth, approve with the desk password.

**Claude Code and scripts:** set a long random `NORTHCHART_MCP_TOKEN` (24+ characters) on the server, then
`claude mcp add --transport http northchart https://<your-host>/mcp --header "Authorization: Bearer <token>"`.

**Server settings** (in `/etc/signaldesk.env`, then restart the service):

| Variable | Purpose |
|---|---|
| `NORTHCHART_PUBLIC_URL` | The public base URL, for example `https://desktrading-today.precifarm.com`. Recommended behind Cloudflare so OAuth metadata always names the right host |
| `NORTHCHART_OWNER_PASS` | Optional consent password; defaults to `SIGNALDESK_PASS` |
| `NORTHCHART_MCP_TOKEN` | Optional fixed bearer token for header-based clients |
| `NORTHCHART_REDIRECT_ALLOW` | Optional extra redirect URI prefixes |

`/mcp`, `/.well-known/oauth-*` and `/oauth/*` bypass HTTP Basic auth because they handle their own; every other route still needs the desk login.

**Agent desk.** The Agents button in the header (or Ctrl+K, or `/`) opens a panel with three tabs: Run, a streaming chat (`northchart_agent.py`, served at `POST /api/agent/stream` as Server-Sent Events): each answer shows the tools it ran as they run, draws interactive 15-minute candle charts with the desk's entry, stop and target lines (or a conviction chart for market scans), then streams the written answer. It runs on the built-in desk engine, which writes only from tool output. Set `ANTHROPIC_API_KEY` on the server to have Claude run the conversation with the same read-only tools instead (`NORTHCHART_AGENT_MODEL` picks the model, default `claude-sonnet-5-5`; any API failure falls back to the desk engine for that answer); Activity (every tool call by the UI, ChatGPT or Claude, with timing and errors) and Connect (the MCP URL, setup steps, connected agents and a sign-out-all button). UI calls go through `/api/agent/run`, `/api/agent/activity`, `/api/agent/status` and `/api/agent/revoke`, which stay behind the desk login.

Tests: `python -m unittest tests.test_connect` runs the full OAuth flow and MCP calls against a real local server.

## API

| Route | Purpose |
|---|---|
| `/api/signal?sym=XAUUSD` | full state for one instrument |
| `/api/roster` | every instrument plus the governor |
| `/api/desk/brief?sym=XAUUSD` | Precifarm analyst brief (plain text; `format=json`) |
| `/api/ask?sym=..&q=..` | rule-based copilot from live state |
| `/api/health` | unauthenticated health probe |
| `/mcp` | Northcharts Connect MCP endpoint (OAuth or bearer token) |
| `/api/agent/*` | agent desk: streaming chat (`/stream`), run a tool, activity feed, status, sign out agents |
| `/tracker` | trading tracker page |
| `/api/tracker/summary` | journal metrics, prop status, prop economics, capital (syncs paper tickets) |
| `/api/tracker/records?kind=` / `export?kind=` / `backup` | read rows, CSV export, zip backup |
| `/api/tracker/append` (POST) | append one validated row `{kind, row}` |
| `/api/tracker/size`, `/api/tracker/drawdown` (POST) | position size and prop drawdown calculators |
| `/api/tracker/import/fills` (POST), `/api/tracker/fills/pending` | closed broker or TradingView trades into the journal inbox |
| `/api/tracker/workbook.xlsx` | Excel workbook with live formulas, including net worth |
| `/router` | live and prop router page |
| `/api/router/route?sym=XAUUSD` | route the desk's current signal across all accounts |
| `/api/router/route` (POST) | route a manual idea `{symbol, direction, entry, stop, tp1}` |
| `/api/router/decision` (POST) | mark a ticket `{route_id, account, decision: TAKEN/SKIPPED/MISSED}` |
| `/api/router/accounts`, `/api/router/account` (POST) | portfolio accounts; update balances, status or risk |
| `/api/router/log`, `/api/router/signals.csv` | recent routes and decisions; CSV for the Signals sheet |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SIGNALDESK_PASS` / `SIGNALDESK_USER` | unset / `trader` | the old desk password: proves ownership at `/setup`, and works for scripts this release |
| `NORTHCHART_SIGNUP` | `invite` | `invite`, `open` or `closed` |
| `NORTHCHART_LEGACY_BASIC` | `1` | `0` stops accepting the old desk password |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` | unset | email for verification and password reset |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | unset | switches on Sign in with Google |
| `NORTHCHART_SECRET_KEY` | generated file | key that seals broker secrets (24+ characters) |
| `NORTHCHART_METAAPI_TOKEN` | unset | switches on the investor-password route via MetaApi |
| `DATA_DIR` | app dir | where journals and state persist |
| `DESK_DAILY_STOP_R` | `4` | desk halts at this daily loss in R |
| `DESK_MAX_OPEN` | `4` | maximum open or working tickets across the desk |
| `DESK_MAX_PER_CLUSTER` | `1` | maximum open tickets per correlated group |
| `DESK_LOSS_STREAK_TIGHTEN` | `2` | consecutive losses before A-grade only |
| `DESK_TZ` | `Africa/Nairobi` | trading-day boundary |
| `DESK_ACCOUNT_BALANCE`, `DESK_RISK_PER_TRADE_PCT` | `0`, `0.5` | lot sizing in the analyst brief |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | unset | optional router alerts for new EXECUTE signals |

Deployment guides live in `deploy/`. Analyst protocol: `docs/PRECIFARM_DESK_ANALYST.md`.

Educational and decision-support tool. Not financial advice.
