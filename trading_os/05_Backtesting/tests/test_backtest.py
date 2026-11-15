"""Backtester tests. All bars here are SYNTHETIC test fixtures."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gtos_backtest.data import load_mt5_bars, resample, synthetic_gold, quality_report  # noqa: E402
from gtos_backtest.engine import Costs, run  # noqa: E402
from gtos_backtest.metrics import metrics  # noqa: E402
from gtos_backtest.strategies import STRATEGIES, s001_trend_pullback  # noqa: E402
from gtos_backtest.validate import evaluate  # noqa: E402


def bars(rows, start="2026-01-05 10:00"):
    idx = pd.date_range(start, periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(spread=0.0)


def sig(t, d, stop, rr=2.0, target=np.nan, max_bars=10):
    return pd.DataFrame([[d, stop, target, rr, max_bars]], index=[t], columns=["direction", "stop", "target", "rr", "max_bars"])


ZERO = Costs(spread=0, slippage=0, commission_per_lot=0)


def test_enters_next_open_and_hits_target():
    df = bars([[100, 101, 99, 100], [100, 100.5, 99.5, 100], [100, 102.5, 99.8, 102], [102, 103, 101, 102]])
    t = run(df, sig(df.index[0], 1, 99.0), ZERO)
    assert t.iloc[0]["entry"] == 100 and t.iloc[0]["reason"] == "target"
    assert t.iloc[0]["r"] == pytest.approx(2.0)


def test_stop_first_when_both_in_one_bar():
    df = bars([[100, 100, 100, 100], [100, 100.2, 99.9, 100], [100, 103, 98, 100]])
    t = run(df, sig(df.index[0], 1, 99.0), ZERO)
    assert t.iloc[0]["reason"] == "stop" and t.iloc[0]["r"] == pytest.approx(-1.0)


def test_gap_through_stop_fills_at_open():
    df = bars([[100, 100, 100, 100], [100, 100.5, 99.5, 100], [97, 97.5, 96, 97]])
    t = run(df, sig(df.index[0], 1, 99.0), ZERO)
    assert t.iloc[0]["exit"] == 97 and t.iloc[0]["r"] == pytest.approx(-3.0)


def test_costs_reduce_r():
    df = bars([[100, 101, 99, 100], [100, 100.5, 99.5, 100], [100, 102.5, 99.8, 102]]).drop(columns="spread")  # use Costs.spread
    c = Costs(spread=0.2, slippage=0.1, commission_per_lot=7, contract_size=100)
    t = run(df, sig(df.index[0], 1, 99.0), c)
    # fill 100.2, risk 1.2, target 102.6 not reached in bar 2 (high 102.5) -> time exit at close 102 - 0.2
    assert t.iloc[0]["entry"] == pytest.approx(100.2)
    assert t.iloc[0]["r"] < (102 - 100) / 1.0


def test_no_lookahead_signals_do_not_change_when_future_changes():
    df = synthetic_gold(6000, seed=4)
    cut = df.index[4000]
    a = s001_trend_pullback(df)
    future_changed = df.copy()
    future_changed.loc[future_changed.index > cut, ["open", "high", "low", "close"]] *= 1.3
    b = s001_trend_pullback(future_changed)
    a_past = a[a.index <= cut - pd.Timedelta(hours=2)]
    b_past = b[b.index <= cut - pd.Timedelta(hours=2)]
    pd.testing.assert_frame_equal(a_past, b_past)


def test_resample_labels_at_close():
    df = bars([[1, 2, 0.5, 1.5]] * 8, start="2026-01-05 10:00")
    h = resample(df, "1h")
    assert h.index[0] == pd.Timestamp("2026-01-05 11:00", tz="UTC")


def test_mt5_loader_converts_server_time(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n2026.01.05\t12:00:00\t2650\t2651\t2649\t2650.5\t100\t0\t30\n")
    df = load_mt5_bars(p, server_utc_offset_hours=2)
    assert df.index[0] == pd.Timestamp("2026-01-05 10:00", tz="UTC")
    assert df["spread"].iloc[0] == pytest.approx(0.30)


def test_metrics_and_gate_fails_on_synthetic():
    df = synthetic_gold(30000, seed=2)
    res, trades = evaluate(df, "S-001", Costs(), 0.7, "test", synthetic=True)
    assert res["gate"]["passed"] is False
    assert any("SYNTHETIC" in f for f in res["gate"]["failures"])
    m = metrics(pd.DataFrame({"r": [2, -1, -1, 3], "direction": ["BUY"] * 4, "exit_time": pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC")}))
    assert m["expectancy_r"] == 0.75 and m["profit_factor"] == 2.5 and m["max_drawdown_r"] == 2.0


@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_every_strategy_runs(name):
    df = synthetic_gold(8000, seed=9)
    s = STRATEGIES[name](df)
    assert set(s.columns) >= {"direction", "stop", "rr", "max_bars"}
    run(df, s, Costs())


def test_quality_report():
    q = quality_report(synthetic_gold(500), "t")
    assert q.timeframe_minutes == 15 and q.bad_ohlc == 0
