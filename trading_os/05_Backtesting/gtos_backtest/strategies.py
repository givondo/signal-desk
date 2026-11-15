"""Strategy signal generators.

Contract: each function takes M15 bars (UTC index) and returns a DataFrame of
signals indexed by the bar whose CLOSE confirms the setup, with columns:
    direction  +1 long / -1 short
    stop       stop-loss price
    target     take-profit price (or NaN to use rr x risk from the fill)
    rr         reward multiple used when target is NaN
    max_bars   time stop in bars
The engine fills at the NEXT bar's open, so no signal can use information from
the bar it trades on. Higher-timeframe values are shifted so only completed
bars are visible.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import resample


# ---------------------------------------------------------------- indicators
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    a = atr(df, n)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def london_ny_mask(idx: pd.DatetimeIndex) -> pd.Series:
    """True when both London (08:00-17:00) and New York (08:00-17:00) are open, DST-aware."""
    lon = idx.tz_convert("Europe/London")
    ny = idx.tz_convert("America/New_York")
    lm = lon.hour * 60 + lon.minute
    nm = ny.hour * 60 + ny.minute
    wd = lon.dayofweek < 5
    return pd.Series(wd & (lm >= 480) & (lm < 1020) & (nm >= 480) & (nm < 1020), index=idx)


def _htf(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Higher-timeframe bars labelled at close, forward-filled onto the base index (completed bars only)."""
    h = resample(df, rule)
    return h


def _align(h: pd.DataFrame | pd.Series, idx: pd.DatetimeIndex) -> pd.DataFrame | pd.Series:
    # A higher-timeframe bar labelled 10:00 closed at 10:00; a base bar labelled 10:00 opened at 10:00,
    # so its own close is 10:15 and it may use the 10:00 H1 bar. reindex with ffill gives exactly that.
    return h.reindex(idx, method="ffill")


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["direction", "stop", "target", "rr", "max_bars"])


# ---------------------------------------------------------------- S-001 trend pullback
def s001_trend_pullback(df: pd.DataFrame, ema_fast: int = 20, ema_slow_h1: int = 50, adx_min: float = 20,
                        touch_atr: float = 0.25, stop_atr: float = 0.25, rr: float = 2.0, max_bars: int = 32,
                        max_per_day: int = 2) -> pd.DataFrame:
    h1 = _htf(df, "1h")
    h1_ema = ema(h1["close"], ema_slow_h1)
    trend_up = (h1["close"] > h1_ema) & (h1_ema > h1_ema.shift(5)) & (adx(h1) > adx_min)
    trend_dn = (h1["close"] < h1_ema) & (h1_ema < h1_ema.shift(5)) & (adx(h1) > adx_min)
    up = _align(trend_up.astype(float), df.index).fillna(0).astype(bool)
    dn = _align(trend_dn.astype(float), df.index).fillna(0).astype(bool)

    e = ema(df["close"], ema_fast)
    a = atr(df)
    session = london_ny_mask(df.index)
    touched_long = df["low"] <= e + touch_atr * a
    touched_short = df["high"] >= e - touch_atr * a
    conf_long = (df["close"] > df["open"]) & (df["close"] > df["high"].shift(1)) & touched_long.shift(1).fillna(False)
    conf_short = (df["close"] < df["open"]) & (df["close"] < df["low"].shift(1)) & touched_short.shift(1).fillna(False)
    swing_low = df["low"].rolling(5).min()
    swing_high = df["high"].rolling(5).max()

    rows = []
    for t in df.index[(up & conf_long & session) | (dn & conf_short & session)]:
        d = 1 if up.loc[t] else -1
        stop = swing_low.loc[t] - stop_atr * a.loc[t] if d == 1 else swing_high.loc[t] + stop_atr * a.loc[t]
        if np.isnan(stop):
            continue
        rows.append((t, d, stop, np.nan, rr, max_bars))
    return _cap_per_day(rows, max_per_day)


# ---------------------------------------------------------------- S-002 pre-New York range breakout
def s002_range_breakout(df: pd.DataFrame, range_start_london: int = 10, range_end_london: int = 13,
                        entry_until_london: int = 15, exit_london: int = 17, min_atr: float = 0.6, max_atr: float = 1.6,
                        rr: float = 1.5) -> pd.DataFrame:
    lon = df.index.tz_convert("Europe/London")
    h1_atr = _align(atr(_htf(df, "1h")), df.index)
    rows = []
    for day, g in df.groupby(lon.date):
        lg = g.index.tz_convert("Europe/London")
        if lg[0].dayofweek >= 5:
            continue
        rng = g[(lg.hour >= range_start_london) & (lg.hour < range_end_london)]
        if len(rng) < 8:
            continue
        hi, lo = rng["high"].max(), rng["low"].min()
        height = hi - lo
        a = h1_atr.loc[rng.index[-1]]
        if not (np.isfinite(a) and min_atr * a <= height <= max_atr * a):
            continue
        window = g[(lg.hour >= range_end_london) & (lg.hour < entry_until_london)]
        closes = window["close"].values
        for i in range(1, len(window)):
            prev, cur = closes[i - 1], closes[i]
            d = 1 if prev > hi and cur > hi else -1 if prev < lo and cur < lo else 0
            if d:
                t = window.index[i]
                mid = (hi + lo) / 2
                exit_bars = int(((g.index.tz_convert("Europe/London").hour < exit_london) & (g.index > t)).sum())
                target = cur + d * min(rr * abs(cur - mid), height)
                rows.append((t, d, mid, target, rr, max(1, exit_bars)))
                break
    return _cap_per_day(rows, 1)


# ---------------------------------------------------------------- liquidity sweep of the previous day's high/low
def sweep_reversal(df: pd.DataFrame, stop_atr: float = 0.1, rr: float = 2.0, max_bars: int = 24) -> pd.DataFrame:
    """HYPOTHESIS under test: price trades through the prior day's high (low), then an M15 bar closes back
    inside. Short (long) with the stop beyond the sweep extreme. Defined mechanically so it can be falsified."""
    daily = df.resample("1D", label="right", closed="left").agg({"high": "max", "low": "min"}).dropna()
    pdh = _align(daily["high"], df.index)
    pdl = _align(daily["low"], df.index)
    a = atr(df)
    session = london_ny_mask(df.index) | pd.Series(df.index.tz_convert("Europe/London").hour.isin(range(7, 17)), index=df.index)
    rows = []
    short = (df["high"] > pdh) & (df["close"] < pdh) & session
    long = (df["low"] < pdl) & (df["close"] > pdl) & session
    for t in df.index[short | long]:
        d = -1 if short.loc[t] else 1
        stop = df.at[t, "high"] + stop_atr * a.loc[t] if d == -1 else df.at[t, "low"] - stop_atr * a.loc[t]
        if np.isfinite(stop):
            rows.append((t, d, stop, np.nan, rr, max_bars))
    return _cap_per_day(rows, 1)


# ---------------------------------------------------------------- random baseline
def random_baseline(df: pd.DataFrame, reference: pd.DataFrame, seed: int = 7) -> pd.DataFrame:
    """Same number of trades, same stop distances, same exits, random times and directions."""
    if reference.empty:
        return _empty()
    rng = np.random.default_rng(seed)
    session = df.index[london_ny_mask(df.index).values]
    times = np.sort(rng.choice(session[:-40], size=min(len(reference), len(session) - 40), replace=False))
    rows = []
    dists = (reference["stop"] - df["close"].reindex(reference.index)).abs().values
    for i, t in enumerate(times):
        d = int(rng.choice([-1, 1]))
        dist = dists[i % len(dists)]
        c = df.at[t, "close"]
        rows.append((t, d, c - d * dist, np.nan, float(reference["rr"].iloc[i % len(reference)]), int(reference["max_bars"].iloc[i % len(reference)])))
    return _cap_per_day(rows, 99)


def _cap_per_day(rows: list[tuple], max_per_day: int) -> pd.DataFrame:
    if not rows:
        return _empty()
    s = pd.DataFrame(rows, columns=["time", "direction", "stop", "target", "rr", "max_bars"]).set_index("time").sort_index()
    s = s[~s.index.duplicated()]
    day = s.index.tz_convert("Africa/Nairobi").date
    s["_n"] = s.groupby(day).cumcount()
    return s[s["_n"] < max_per_day].drop(columns="_n")


STRATEGIES = {"S-001": s001_trend_pullback, "S-002": s002_range_breakout, "SWEEP": sweep_reversal}


# Setups from the trader research (trading_os/14_Trader_Research), registered alongside the originals.
from .champions import CHAMPION_STRATEGIES  # noqa: E402

STRATEGIES.update(CHAMPION_STRATEGIES)
