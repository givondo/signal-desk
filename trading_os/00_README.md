# GIVONDO Trading OS

Version 1.0, 13 November 2026. A professional trading desk run by one person: research, risk, records and reviews in one folder. Capital preservation precedes capital growth.

## Status by phase

| Phase | Status | Where |
|---|---|---|
| 1 Discovery | Baseline built from existing records; 15 questions open | `01_Trading_Profile_and_Objectives.md` |
| 2 Prop firm research | Done: 5 firms researched, scored and ranked; Topstep excluded (Kenya ineligible) | `02_Prop_Firm_Intelligence.md` |
| 3 Personal capital framework | Rules done; amounts pending your answers | `03_Personal_Capital_Management.md` |
| 4 Strategy development | Market focus, validation gate and two strategy specs done; real-data testing next | `04_Trading_Strategies_and_Research.md` |
| 5 Risk management | Policy plus tested position-sizing and drawdown calculators | `06_Risk_Management/` |
| 6 Journal and trackers | CSV templates ready, append-only seal in place | `07` to `09` |
| 7 Analytics | Metrics, prop economics, capital and dashboard generator | `11_Performance_Analytics/tos_analytics.py` |
| 8 Routines | Daily, weekly and monthly routines below; review templates | `13_Decisions_and_Reviews/` |
| 9 Validation | 27 tests against hand-worked figures pass | `tests/` |
| 10 Continuous improvement | Starts with real data | Decision log |

## Tracker

Inside Signal Desk, open `/tracker` to log trades, prop purchases, account snapshots, payouts, deposits, withdrawals and psychology entries through forms. It writes to the same CSV layout as this folder, under `DATA_DIR/trading_os_data/` on the desk server, and the analytics below read it with `TOS_DATA_DIR` pointed at that folder. This repository is public: keep real records in the data folder, never in these template files.

## Setup (Windows, Python 3.10+)

```powershell
cd <path>\GIVONDO_TRADING_OS
py -m pip install pytest          # only needed to run the tests
py -m pytest -q                   # expect 27 passed
py 06_Risk_Management\Drawdown_Calculator.py --list
```

The calculators and analytics use only the Python standard library.

## Daily operating routine

**Before the session (by 15:30 EAT)**
1. Read today's events in `10_Market_Intelligence/Economic_Calendar.md`; fill the pre-market brief.
2. Note equity (not balance) for each account.
3. Run the drawdown calculator for each prop account; write down the daily floor, internal stop and remaining allowance in money.
4. Check the risk limits for the day and the no-entry news windows.
5. Re-read the entry criteria of the strategies you will trade.
6. Decide: TRADE, REDUCED RISK or NO TRADE.

**During the session**
- Only setups that meet a written specification. Size every trade with `Position_Sizing.py`.
- Record each trade as it closes.
- Watch aggregate and correlated open risk.
- Follow the loss-streak and break rules in the Risk Policy.

**After the session**
1. Complete journal rows: P/L net of costs, R multiple, screenshots before and after, rule compliance, emotional state, lesson.
2. Record missed trades and rule violations in their own files.
3. Fill the day's row in `Psychology_Log.csv`.
4. `py 11_Performance_Analytics\tos_analytics.py seal` to lock the day's rows.
5. Check platform figures against the journal.

**Weekly** (weekend): run the dashboard, complete the weekly review template.

**Monthly** (first weekend): reconcile statements, run the dashboard, write the monthly report and the monthly review.

## Safeguards that apply everywhere

Never fabricate results. Never guarantee profit. No revenge trading. No risk increase to recover losses. Nominal prop capital is never personal wealth. Pending payouts are not cash. Costs, spread and slippage are always counted. No essential savings, borrowed money or Precifarm funds. Failed challenges and losses are never hidden or deleted. Backtests are never presented as live results. Nothing in this OS places trades. Firm rules are re-verified before every purchase. Marketing claims are not evidence. Strategy rules are never changed retrospectively.

## Credentials

Never store broker logins, investor passwords, API keys or prop firm passwords in this folder, in the CSVs, in prompts or in logs. Use a password manager. Account labels in the trackers are internal names such as `FTMO-2S-10K-01`, not account numbers.

## Integrations (later)

MT5 history exports (CSV or HTML report) and broker statements are the first import route. Signal Desk already has a read-only MT5 adapter and TradingView data paths. Check API access, permissions and costs before adding any integration; most prop firms do not offer an API. Deterministic code calculates; AI explains.

## Folder map

```
00_README.md                          this file
01_Trading_Profile_and_Objectives.md  who, what is verified, open questions
02_Prop_Firm_Intelligence.md          firm comparison, scores, economics
03_Personal_Capital_Management.md     allocation, brokers, records, tax notes
04_Trading_Strategies_and_Research.md market focus, validation gate, strategy specs
05_Trading_Financial_Performance.md   ledgers, definitions, monthly report template
06_Risk_Management/                   Risk_Policy.md, risk_limits.json, Position_Sizing.py, Drawdown_Calculator.py
07_Trading_Journal/                   trades (append-only), corrections, missed trades, violations, psychology
08_Prop_Firm_Accounts/                evaluations, funded accounts, payouts
09_Personal_Capital/                  accounts, deposits, withdrawals, reconciliation
10_Market_Intelligence/               economic calendar, daily research
11_Performance_Analytics/             tos_analytics.py, dashboards, monthly reports, strategy results
12_Learning_and_Development/          roadmap, competency tracker
13_Decisions_and_Reviews/             decision log, weekly and monthly reviews
tests/                                hand-worked validation tests
```

## Backups

Keep this folder in a private Git repository and push after each journaling session; the commit history is an independent audit trail on top of the journal seal. Keep a second copy in cloud storage.
