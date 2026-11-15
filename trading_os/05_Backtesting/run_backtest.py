"""Run a strategy backtest with validation and write results.

Examples (Windows PowerShell, from 05_BACKTESTING):
  py run_backtest.py --data data\\XAUUSD_M15.csv --format mt5 --offset 2 --strategy S-001
  py run_backtest.py --data data\\XAUUSD_M15.csv --format mt5 --strategy S-002 --spread 0.35 --commission 7
  py run_backtest.py --synthetic --strategy S-001        # code check only; gate always fails

Outputs in results/: <strategy>_<date>.json (upload to the app's Research library as BACKTEST)
and <strategy>_<date>_trades.csv.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from gtos_backtest.data import load_csv, load_mt5_bars, quality_report, synthetic_gold
from gtos_backtest.engine import Costs
from gtos_backtest.strategies import STRATEGIES
from gtos_backtest.validate import evaluate


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data")
    ap.add_argument("--format", choices=["mt5", "csv"], default="mt5")
    ap.add_argument("--offset", type=float, default=2.0, help="MT5 server UTC offset in hours")
    ap.add_argument("--point", type=float, default=0.01, help="Price value of one MT5 spread point")
    ap.add_argument("--strategy", choices=sorted(STRATEGIES), default="S-001")
    ap.add_argument("--spread", type=float, default=0.30)
    ap.add_argument("--slippage", type=float, default=0.10)
    ap.add_argument("--commission", type=float, default=0.0, help="Round-turn commission per lot")
    ap.add_argument("--contract", type=float, default=100.0)
    ap.add_argument("--split", type=float, default=0.7)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()

    if a.synthetic:
        df, source = synthetic_gold(60000), "synthetic random walk"
    elif a.data:
        df = load_mt5_bars(a.data, a.offset, a.point) if a.format == "mt5" else load_csv(a.data)
        source = Path(a.data).name
    else:
        ap.error("--data or --synthetic is required")
    q = quality_report(df, source)
    print(f"Data: {q.bars} bars {q.start} to {q.end}, {q.timeframe_minutes}-min, weekday gaps {q.gaps_over_3x}, duplicates {q.duplicate_times}, bad OHLC {q.bad_ohlc}")
    if q.timeframe_minutes != 15:
        print("Warning: strategies are specified on M15 bars; results on other timeframes are not comparable.")

    costs = Costs(a.spread, a.slippage, a.commission, a.contract)
    result, trades = evaluate(df, a.strategy, costs, a.split, source, a.synthetic)
    result["data_quality"] = q.__dict__
    result["generated_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    out = Path(a.out)
    out.mkdir(exist_ok=True)
    stem = f"{a.strategy}_{datetime.now():%Y%m%d_%H%M}{'_SYNTHETIC' if a.synthetic else ''}"
    (out / f"{stem}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    trades.to_csv(out / f"{stem}_trades.csv", index=False)

    for k in ("in_sample", "out_of_sample", "random_baseline"):
        m = result[k]
        print(f"{k:16s} n={m.get('n', 0):4d}  exp={m.get('expectancy_r')}R  PF={m.get('profit_factor')}  maxDD={m.get('max_drawdown_r')}R")
    print("GATE:", "PASSED" if result["gate"]["passed"] else "FAILED")
    for f in result["gate"]["failures"]:
        print("  -", f)
    print(f"Written {out / (stem + '.json')}")


if __name__ == "__main__":
    main()
