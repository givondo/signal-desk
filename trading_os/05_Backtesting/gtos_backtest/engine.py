"""Bar-by-bar execution simulator with conservative assumptions.

* Entry at the next bar's open, plus half the spread and the slippage against you.
* If stop and target are both inside one bar, the STOP is assumed to fill first.
* Gaps through the stop fill at the bar's open (worse than the stop).
* Exits pay half the spread and slippage again. Commission is charged per round turn.
* One position at a time; signals during an open trade are skipped.
Results are in R: net price result divided by the risk from the actual fill to the stop.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Costs:
    spread: float = 0.30  # price units, used when the data has no spread column
    slippage: float = 0.10  # price units per side
    commission_per_lot: float = 0.0  # round turn, quote currency
    contract_size: float = 100.0  # oz per lot, to express commission in price units

    @property
    def commission_price(self) -> float:
        return self.commission_per_lot / self.contract_size


def run(df: pd.DataFrame, signals: pd.DataFrame, costs: Costs = Costs()) -> pd.DataFrame:
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    sp = df["spread"].fillna(costs.spread).to_numpy() if "spread" in df else np.full(len(df), costs.spread)
    pos = {t: i for i, t in enumerate(df.index)}
    trades = []
    busy_until = -1
    for t, s in signals.iterrows():
        i = pos.get(t)
        if i is None or i + 1 >= len(df) or i < busy_until:
            continue
        d = int(s["direction"])
        k = i + 1
        fill = o[k] + d * (sp[k] / 2 + costs.slippage)
        stop = float(s["stop"])
        risk = (fill - stop) * d
        if risk <= 0:
            continue  # stop already passed at the open: no trade
        target = float(s["target"]) if np.isfinite(s["target"]) else fill + d * float(s["rr"]) * risk
        last = min(len(df) - 1, k + int(s["max_bars"]) - 1)
        exit_px, reason, j = None, "time", last
        for j in range(k, last + 1):
            stop_hit = l[j] <= stop if d == 1 else h[j] >= stop
            tgt_hit = h[j] >= target if d == 1 else l[j] <= target
            if stop_hit:
                gap = (o[j] < stop) if d == 1 else (o[j] > stop)
                exit_px, reason = (o[j] if gap and j > k else stop), "stop"
                break
            if tgt_hit:
                exit_px, reason = target, "target"
                break
        if exit_px is None:
            exit_px = c[last]
        exit_fill = exit_px - d * (sp[j] / 2 + costs.slippage)
        gross = (exit_fill - fill) * d
        net = gross - costs.commission_price
        trades.append(
            {
                "signal_time": t, "entry_time": df.index[k], "exit_time": df.index[j], "direction": "BUY" if d == 1 else "SELL",
                "entry": round(fill, 3), "stop": round(stop, 3), "target": round(target, 3), "exit": round(exit_fill, 3),
                "risk_price": round(risk, 4), "reason": reason, "r": net / risk, "r_gross": ((exit_px - o[k]) * d) / max(1e-12, (o[k] - stop) * d),
            }
        )
        busy_until = j + 1
    return pd.DataFrame(trades)
