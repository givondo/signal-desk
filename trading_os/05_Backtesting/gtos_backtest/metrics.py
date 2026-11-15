"""Performance metrics in R, matching the definitions used by the Trading OS app."""
from __future__ import annotations

import numpy as np
import pandas as pd


def metrics(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {"n": 0}
    r = trades["r"].to_numpy(dtype=float)
    wins, losses = r[r > 0], r[r < 0]
    cum = np.cumsum(r)
    peak = np.maximum.accumulate(np.r_[0, cum])[1:]
    dd = float(np.max(peak - cum)) if len(cum) else 0.0
    streak = mx = 0
    for x in r:
        streak = streak + 1 if x < 0 else 0
        mx = max(mx, streak)
    sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    mean = float(np.mean(r))
    t = pd.to_datetime(trades["exit_time"])
    weeks = max((t.max() - t.min()).days / 7, 1e-9)
    monthly = trades.assign(m=t.dt.strftime("%Y-%m")).groupby("m")["r"].sum()
    pos_total = monthly[monthly > 0].sum()
    return {
        "n": int(len(r)),
        "win_rate": round(float(len(wins) / len(r)), 4),
        "avg_win_r": round(float(wins.mean()), 3) if len(wins) else None,
        "avg_loss_r": round(float(losses.mean()), 3) if len(losses) else None,
        "expectancy_r": round(mean, 4),
        "expectancy_r_lower95": round(mean - 1.96 * sd / np.sqrt(len(r)), 4) if len(r) > 1 else None,
        "profit_factor": round(float(wins.sum() / -losses.sum()), 3) if len(losses) and losses.sum() < 0 else None,
        "total_r": round(float(r.sum()), 2),
        "max_drawdown_r": round(dd, 2),
        "recovery_factor": round(float(r.sum()) / dd, 2) if dd > 0 else None,
        "max_consecutive_losses": int(mx),
        "trades_per_week": round(len(r) / weeks, 2),
        "sharpe_per_trade": round(mean / sd, 3) if sd > 0 else None,
        "long": _side(trades, "BUY"),
        "short": _side(trades, "SELL"),
        "best_month_share_of_profit": round(float(monthly.max() / pos_total), 3) if pos_total > 0 else None,
        "monthly_r": {k: round(float(v), 2) for k, v in monthly.items()},
    }


def _side(trades: pd.DataFrame, side: str) -> dict:
    s = trades[trades["direction"] == side]["r"]
    return {"n": int(len(s)), "expectancy_r": round(float(s.mean()), 4) if len(s) else None}


def by_session(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    lon = pd.to_datetime(trades["entry_time"]).dt.tz_convert("Europe/London")
    label = np.where(lon.dt.hour < 13, "London morning", np.where(lon.dt.hour < 17, "Overlap", "New York afternoon"))
    return {k: metrics(g) for k, g in trades.assign(s=label).groupby("s")}


def monte_carlo_drawdown(r: np.ndarray, risk_pct: float, runs: int = 5000, seed: int = 3) -> dict:
    """Bootstrap the R sequence to estimate the drawdown distribution in % of equity at a given risk."""
    rng = np.random.default_rng(seed)
    if len(r) == 0:
        return {}
    dds = np.empty(runs)
    for i in range(runs):
        seq = rng.choice(r, size=len(r), replace=True) * risk_pct
        cum = np.cumsum(seq)
        dds[i] = np.max(np.maximum.accumulate(np.r_[0, cum])[1:] - cum)
    return {"median_dd_pct": round(float(np.median(dds)), 2), "p95_dd_pct": round(float(np.percentile(dds, 95)), 2)}
