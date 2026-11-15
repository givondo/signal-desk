"""Tests for the researched setups (S-011 to S-016, PTJ filter). All bars are SYNTHETIC fixtures."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gtos_backtest.champions import CHAMPION_STRATEGIES, ptj_200d_filter, s011_volatility_breakout  # noqa: E402
from gtos_backtest.data import synthetic_gold  # noqa: E402
from gtos_backtest.engine import run  # noqa: E402
from gtos_backtest.strategies import STRATEGIES  # noqa: E402
from gtos_backtest.validate import evaluate  # noqa: E402

DF = synthetic_gold(14000, seed=11)


@pytest.mark.parametrize("name", list(CHAMPION_STRATEGIES))
def test_signals_are_well_formed(name):
    s = STRATEGIES[name](DF)
    assert set(["direction", "stop", "target", "rr", "max_bars"]) <= set(s.columns)
    if s.empty:
        return
    assert s.index.isin(DF.index).all()
    assert set(s["direction"].unique()) <= {1, -1}
    px = DF["close"].reindex(s.index)
    # the stop is on the losing side of the signal bar's close
    assert (((s["direction"] == 1) & (s["stop"] < px)) | ((s["direction"] == -1) & (s["stop"] > px))).all()
    assert (s["max_bars"] >= 1).all()
    t = run(DF, s)
    assert t.empty or np.isfinite(t["r"]).all()


@pytest.mark.parametrize("name", ["S-011", "S-012", "S-013", "S-014", "S-015", "S-016"])
def test_no_lookahead(name):
    fn = STRATEGIES[name]
    cut = DF.index[9000]
    a = fn(DF)
    future = DF.copy()
    later = future.index > cut + pd.Timedelta(days=1)
    future.loc[later, ["open", "high", "low", "close"]] *= 1.07
    b = fn(future)
    early = cut - pd.Timedelta(days=1)
    pd.testing.assert_frame_equal(a[a.index < early], b[b.index < early], check_dtype=False)


def test_some_setups_fire_on_synthetic_data():
    fired = {n: len(STRATEGIES[n](DF)) for n in ("S-011", "S-012", "S-013", "S-014")}
    assert all(v > 0 for v in fired.values()), fired


def test_volatility_breakout_levels():
    # Day 1: range 10 (2000-2010). Day 2 London open 2005; k=0.5 trigger 2010; close 2011 triggers a long.
    idx = pd.date_range("2026-01-05 00:00", "2026-01-06 23:45", freq="15min", tz="UTC")
    df = pd.DataFrame({"open": 2005.0, "high": 2005.5, "low": 2004.5, "close": 2005.0, "spread": 0.3}, index=idx)
    d1 = df.index.date == pd.Timestamp("2026-01-05").date()
    df.loc[d1, "high"], df.loc[d1, "low"] = 2010.0, 2000.0
    t = pd.Timestamp("2026-01-06 09:30", tz="UTC")
    df.loc[t, ["high", "close"]] = [2011.5, 2011.0]
    s = s011_volatility_breakout(df, k=0.5, stop_k=0.5)
    assert len(s) == 1 and s.index[0] == t and s.iloc[0]["direction"] == 1
    assert s.iloc[0]["stop"] == pytest.approx(2010 - 5)


def test_ptj_filter_drops_trades_against_the_200_day():
    s = pd.DataFrame({"direction": [1, -1], "stop": [0.0, 1e9], "target": np.nan, "rr": 2.0, "max_bars": 5},
                     index=[DF.index[-200], DF.index[-100]])
    out = ptj_200d_filter(DF, s, length=20)
    sma = DF["close"].resample("1D", label="right", closed="left").last().dropna().rolling(20).mean()
    for t, row in out.iterrows():
        lvl = sma.reindex(DF.index, method="ffill").loc[t]
        assert (row["direction"] == 1 and DF.at[t, "close"] > lvl) or (row["direction"] == -1 and DF.at[t, "close"] < lvl)


def test_gate_runs_and_fails_on_synthetic():
    rep, _ = evaluate(DF, "S-012", synthetic=True)
    assert rep["gate"]["passed"] is False
    assert any("SYNTHETIC" in f for f in rep["gate"]["failures"])
