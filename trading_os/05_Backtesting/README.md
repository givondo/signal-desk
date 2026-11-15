# 05 Backtesting framework

Python, reproducible, cost-aware. Strategies are mechanical definitions of the hypotheses in `trading_os/04_Trading_Strategies_and_Research.md`; nothing here is assumed to work. S-011 to S-016 are setups from the trader research (`trading_os/14_Trader_Research/`), coded in `gtos_backtest/champions.py`.

## Install (Windows)
```powershell
cd signal-desk\trading_os\05_Backtesting
py -m pip install -r requirements.txt
py -m pytest -q tests          # 38 tests, synthetic fixtures only
```

## Get real data
MT5 > View > Symbols > XAUUSD > Bars tab > timeframe M15 > choose at least 2 years > Request > Export Bars. Save to `trading_os\05_Backtesting\data\XAUUSD_M15.csv` (git-ignored). Note the broker's server time zone (usually UTC+2 in winter, UTC+3 in summer) and the symbol's spread point size.

## Run
```powershell
py run_backtest.py --data data\XAUUSD_M15.csv --format mt5 --offset 2 --strategy S-001 --spread 0.30 --slippage 0.10 --commission 0
py run_backtest.py --data data\XAUUSD_M15.csv --strategy S-002
py run_backtest.py --data data\XAUUSD_M15.csv --strategy SWEEP
py run_backtest.py --data data\XAUUSD_M15.csv --strategy S-015   # Crabel NR7 + ORB, compare with S-002
py run_backtest.py --data data\XAUUSD_M15.csv --strategy S-014+PTJ   # Holy Grail with the 200-day filter
```
Each run writes `results/<strategy>_<date>.json` and `_trades.csv`. Upload the JSON in the app (Research library, category BACKTEST); the Strategy lab shows it with the gate verdict.

## What the engine assumes (conservative)
- Fill at the next bar's open plus half spread plus slippage; never on the signal bar.
- Stop first when stop and target are inside one bar; gaps through the stop fill at the open.
- Higher-timeframe bars are labelled at their close and only visible once complete (tested: changing future prices does not change past signals).
- One position at a time; daily attempt caps per strategy.

## Validation gate (all must pass)
At least 2 years of real data; 100 in-sample and 50 out-of-sample trades; OOS expectancy at least +0.15R and profit factor at least 1.3 after costs; still positive with each parameter moved 20%; no month above 30% of profit; beats the random-entry baseline; 95th percentile Monte Carlo drawdown below 5% at 0.25% risk. Then 30 forward demo trades within one standard error of the backtest.

A `--synthetic` run exists only to check the code. Its gate always fails.
