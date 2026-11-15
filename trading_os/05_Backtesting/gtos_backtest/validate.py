"""Validation: chronological in-sample / out-of-sample split, parameter robustness,
random-entry baseline, month concentration and drawdown fit. Produces the gate
verdict used by the Trading OS (decision D-003)."""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from .engine import Costs, run
from .metrics import metrics, monte_carlo_drawdown, by_session
from .strategies import STRATEGIES, random_baseline

GATE = {
    "min_years": 2.0,
    "min_is_trades": 100,
    "min_oos_trades": 50,
    "min_oos_expectancy_r": 0.15,
    "min_oos_profit_factor": 1.3,
    "max_best_month_share": 0.30,
    "planned_risk_pct": 0.25,
    "max_p95_dd_pct": 5.0,  # half of a 10% prop maximum loss
}


def _numeric_params(fn) -> dict:
    sig = inspect.signature(fn)
    return {k: p.default for k, p in sig.parameters.items() if k != "df" and isinstance(p.default, (int, float)) and not isinstance(p.default, bool)}


def evaluate(df: pd.DataFrame, strategy: str, costs: Costs = Costs(), split: float = 0.7, data_source: str = "", synthetic: bool = False,
             params: dict | None = None) -> dict:
    fn = STRATEGIES[strategy]
    params = params or {}
    cut = df.index[int(len(df) * split)]
    # Signals are generated on the full history (indicators need warm-up) but only past data is used per bar;
    # trades are then assigned to IS or OOS by entry time. Parameters are fixed before the OOS result is seen.
    sig = fn(df, **params)
    trades = run(df, sig, costs)
    is_t = trades[pd.to_datetime(trades["entry_time"]) < cut] if not trades.empty else trades
    oos_t = trades[pd.to_datetime(trades["entry_time"]) >= cut] if not trades.empty else trades
    m_is, m_oos, m_all = metrics(is_t), metrics(oos_t), metrics(trades)

    base = run(df, random_baseline(df, sig), costs)
    m_base = metrics(base)

    robustness = {}
    for k, v in _numeric_params(fn).items():
        if k in ("max_per_day",):
            continue
        res = []
        for f in (0.8, 1.2):
            nv = type(v)(round(v * f)) if isinstance(v, int) else v * f
            if isinstance(v, int) and nv == v:
                nv = v + (1 if f > 1 else -1)
            try:
                mm = metrics(run(df, fn(df, **{**params, k: nv}), costs))
                res.append({"value": nv, "n": mm.get("n", 0), "expectancy_r": mm.get("expectancy_r")})
            except Exception as e:  # a parameter combination can be invalid; record it
                res.append({"value": nv, "error": str(e)})
        robustness[k] = res

    years = (df.index.max() - df.index.min()).days / 365.25
    mc = monte_carlo_drawdown(trades["r"].to_numpy() if not trades.empty else np.array([]), GATE["planned_risk_pct"])
    failures = []
    if synthetic:
        failures.append("Data is SYNTHETIC: results prove the code runs, not that an edge exists")
    if years < GATE["min_years"]:
        failures.append(f"Only {years:.1f} years of data (need {GATE['min_years']})")
    if m_is.get("n", 0) < GATE["min_is_trades"]:
        failures.append(f"In-sample trades {m_is.get('n', 0)} < {GATE['min_is_trades']}")
    if m_oos.get("n", 0) < GATE["min_oos_trades"]:
        failures.append(f"Out-of-sample trades {m_oos.get('n', 0)} < {GATE['min_oos_trades']}")
    if (m_oos.get("expectancy_r") or -9) < GATE["min_oos_expectancy_r"]:
        failures.append(f"OOS expectancy {m_oos.get('expectancy_r')}R < {GATE['min_oos_expectancy_r']}R")
    if (m_oos.get("profit_factor") or 0) < GATE["min_oos_profit_factor"]:
        failures.append(f"OOS profit factor {m_oos.get('profit_factor')} < {GATE['min_oos_profit_factor']}")
    if any((r.get("expectancy_r") or -1) <= 0 for rs in robustness.values() for r in rs):
        failures.append("Not robust: expectancy <= 0 when a parameter moves 20%")
    share = m_all.get("best_month_share_of_profit")
    if share is not None and share > GATE["max_best_month_share"]:
        failures.append(f"One month supplies {share:.0%} of profit (max {GATE['max_best_month_share']:.0%})")
    if (m_base.get("expectancy_r") or -9) >= (m_all.get("expectancy_r") or -9):
        failures.append("Does not beat the random-entry baseline with the same exits and costs")
    if mc and mc["p95_dd_pct"] > GATE["max_p95_dd_pct"]:
        failures.append(f"95th percentile drawdown {mc['p95_dd_pct']}% at {GATE['planned_risk_pct']}% risk exceeds {GATE['max_p95_dd_pct']}%")

    return {
        "strategy": strategy,
        "label": "SYNTHETIC TEST RUN" if synthetic else "BACKTEST",
        "data_source": data_source + (" (SYNTHETIC)" if synthetic else ""),
        "period": {"start": str(df.index.min()), "end": str(df.index.max()), "years": round(years, 2), "split_at": str(cut)},
        "costs": costs.__dict__,
        "params": {**_numeric_params(fn), **params},
        "in_sample": m_is,
        "out_of_sample": m_oos,
        "all": m_all,
        "by_session": by_session(trades),
        "random_baseline": m_base,
        "robustness": robustness,
        "monte_carlo": mc,
        "gate": {"passed": not failures, "failures": failures, "thresholds": GATE},
    }, trades
