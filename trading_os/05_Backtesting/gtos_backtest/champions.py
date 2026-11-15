"""Setups taken from traders with audited or published records, written as XAUUSD hypotheses.

Each function follows the strategies.py contract (M15 bars, UTC index, one row per
signal at the bar whose CLOSE confirms it; the engine fills at the next bar's open).
They are faithful to the published rules where the source is specific, and every
adaptation to intraday gold is named in the docstring. None is assumed to work:
each must pass the validation gate (D-003) on real data before any money is used.

Sources and records: trading_os/14_Trader_Research/traders.json.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import resample
from .strategies import _align, _cap_per_day, _empty, adx, atr, ema, london_ny_mask

M15 = pd.Timedelta(minutes=15)


def _london(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return idx.tz_convert("Europe/London")


def _daily(df: pd.DataFrame) -> pd.DataFrame:
    """UTC calendar days, labelled at the close (only completed days are visible)."""
    return resample(df, "1D")


def _htf_to_m15(t: pd.Timestamp) -> pd.Timestamp:
    """A higher-timeframe bar labelled t closed at t; the M15 bar that closes at t is labelled t - 15 min."""
    return t - M15


def _bars_until(g_index: pd.DatetimeIndex, t: pd.Timestamp, london_hour: int) -> int:
    lon = g_index.tz_convert("Europe/London")
    return max(1, int(((g_index > t) & (lon.hour < london_hour)).sum()))


# ------------------------------------------------------------- S-011 Larry Williams volatility breakout
def s011_volatility_breakout(df: pd.DataFrame, k: float = 0.5, stop_k: float = 0.5, rr: float = 2.0,
                             start_london: int = 8, exit_london: int = 20) -> pd.DataFrame:
    """Williams (1987 World Cup, +11,376%): buy when price moves k x yesterday's range above the open,
    sell k x range below it. Adaptation: the 'open' is the London 08:00 price, a move counts only on an
    M15 close beyond the trigger, the stop sits stop_k x range on the other side of the trigger, and any
    open trade is closed by 20:00 London. One trade per day."""
    day = _align(_daily(df)[["high", "low"]], df.index)
    rng = (day["high"] - day["low"]).rename("rng")
    lon = _london(df.index)
    rows = []
    for d, g in df.groupby(lon.date):
        lg = _london(g.index)
        if lg[0].dayofweek >= 5:
            continue
        opening = g[(lg.hour == start_london) & (lg.minute == 0)]
        if opening.empty:
            continue
        t0 = opening.index[0]
        r = rng.loc[t0]
        if not np.isfinite(r) or r <= 0:
            continue
        o = df.at[t0, "open"]
        up, dn = o + k * r, o - k * r
        win = g[(g.index >= t0) & (lg.hour < exit_london)]
        for t, c in win["close"].items():
            dirn = 1 if c > up else -1 if c < dn else 0
            if dirn:
                trig = up if dirn == 1 else dn
                rows.append((t, dirn, trig - dirn * stop_k * r, np.nan, rr, _bars_until(g.index, t, exit_london)))
                break
    return _cap_per_day(rows, 1)


# ------------------------------------------------------------- S-012 Andrea Unger prior-day breakout
def s012_prior_day_breakout(df: pd.DataFrame, stop_atr: float = 1.0, rr: float = 1.5, start_london: int = 8,
                            last_entry_london: int = 16, exit_london: int = 20) -> pd.DataFrame:
    """Unger (World Cup futures winner 2008, 2009, 2010, 2012) starts from simple ideas such as buying a
    break of yesterday's high, and checks the average trade beats costs before anything else.
    Rules here: an M15 close above the prior UTC day's high (below its low) between 08:00 and 16:00
    London; stop stop_atr x H1 ATR beyond the broken level; exit by 20:00 London. One trade per day."""
    day = _align(_daily(df)[["high", "low"]], df.index)
    a1 = _align(atr(resample(df, "1h")), df.index)
    lon = _london(df.index)
    rows = []
    for d, g in df.groupby(lon.date):
        lg = _london(g.index)
        if lg[0].dayofweek >= 5:
            continue
        win = g[(lg.hour >= start_london) & (lg.hour < last_entry_london)]
        for t, c in win["close"].items():
            pdh, pdl, a = day.at[t, "high"], day.at[t, "low"], a1.loc[t]
            if not (np.isfinite(pdh) and np.isfinite(a)):
                continue
            dirn = 1 if c > pdh else -1 if c < pdl else 0
            if dirn:
                lvl = pdh if dirn == 1 else pdl
                rows.append((t, dirn, lvl - dirn * stop_atr * a, np.nan, rr, _bars_until(g.index, t, exit_london)))
                break
    return _cap_per_day(rows, 1)


# ------------------------------------------------------------- S-013 Raschke and Connors Turtle Soup
def s013_turtle_soup(df: pd.DataFrame, lookback: int = 20, min_age: int = 4, stop_atr: float = 0.1,
                     rr: float = 2.0, max_bars: int = 24) -> pd.DataFrame:
    """Turtle Soup (Street Smarts, 1995): a new 20-period low whose previous 20-period low is at least
    4 bars old, which then closes back above that earlier low, is bought (mirror for shorts).
    Adaptation: H1 bars instead of daily, entry on the closing reversal, stop just beyond the new
    extreme, 2R target, 6-hour time stop."""
    h = resample(df, "1h")
    a = atr(h)
    lows, highs, closes = h["low"].to_numpy(), h["high"].to_numpy(), h["close"].to_numpy()
    rows = []
    for i in range(lookback + 1, len(h)):
        wl, wh = lows[i - lookback:i], highs[i - lookback:i]
        prev_low, prev_high = wl.min(), wh.max()
        age_low = lookback - int(np.argmin(wl))  # bars since the earlier extreme
        age_high = lookback - int(np.argmax(wh))
        t = _htf_to_m15(h.index[i])
        if t not in df.index or not np.isfinite(a.iloc[i]):
            continue
        if lows[i] < prev_low and age_low >= min_age and closes[i] > prev_low:
            rows.append((t, 1, lows[i] - stop_atr * a.iloc[i], np.nan, rr, max_bars))
        elif highs[i] > prev_high and age_high >= min_age and closes[i] < prev_high:
            rows.append((t, -1, highs[i] + stop_atr * a.iloc[i], np.nan, rr, max_bars))
    return _cap_per_day(rows, 2)


# ------------------------------------------------------------- S-014 Raschke and Connors Holy Grail
def s014_holy_grail(df: pd.DataFrame, adx_min: float = 30, ema_len: int = 20, stop_atr: float = 0.1,
                    rr: float = 2.0, max_bars: int = 32) -> pd.DataFrame:
    """Holy Grail (Street Smarts, 1995): with the 14-period ADX above 30 and rising, the first pullback
    to the 20-period EMA is bought in an uptrend (sold in a downtrend). Adaptation: H1 bars; entry when
    the touching bar closes back on the trend side of the EMA, stop beyond that bar, 2R target."""
    h = resample(df, "1h")
    e, x, a = ema(h["close"], ema_len), adx(h), atr(h)
    rising = x > x.shift(3)
    up = (e > e.shift(5)) & (h["low"] <= e) & (h["close"] > e)
    dn = (e < e.shift(5)) & (h["high"] >= e) & (h["close"] < e)
    first_touch_up = up & ~up.shift(1, fill_value=False)
    first_touch_dn = dn & ~dn.shift(1, fill_value=False)
    ok = (x > adx_min) & rising & a.notna()
    rows = []
    for t in h.index[ok & (first_touch_up | first_touch_dn)]:
        tm = _htf_to_m15(t)
        if tm not in df.index:
            continue
        d = 1 if first_touch_up.loc[t] else -1
        stop = h.at[t, "low"] - stop_atr * a.loc[t] if d == 1 else h.at[t, "high"] + stop_atr * a.loc[t]
        rows.append((tm, d, stop, np.nan, rr, max_bars))
    return _cap_per_day(rows, 2)


# ------------------------------------------------------------- S-015 Crabel NR7 + opening range breakout
def s015_nr7_orb(df: pd.DataFrame, nr: int = 7, or_minutes: int = 60, start_london: int = 8, exit_london: int = 17,
                 rr: float = 2.0) -> pd.DataFrame:
    """Crabel (Day Trading with Short Term Price Patterns and Opening Range Breakout, 1990): after the
    narrowest daily range of the last 7 (NR7), trade the break of the opening range. Adaptation: the
    opening range is the first 60 minutes from 08:00 London; entry on an M15 close outside it; stop at
    the other side of the range; exit by 17:00 London."""
    d = _daily(df)
    rng = d["high"] - d["low"]
    is_nr = rng <= rng.rolling(nr).min()
    nr_flag = _align(is_nr.astype(float), df.index).fillna(0).astype(bool)
    lon = _london(df.index)
    n_or = max(1, or_minutes // 15)
    rows = []
    for day, g in df.groupby(lon.date):
        lg = _london(g.index)
        if lg[0].dayofweek >= 5:
            continue
        start = g[(lg.hour == start_london) & (lg.minute == 0)]
        if start.empty or not nr_flag.loc[start.index[0]]:
            continue
        i0 = g.index.get_loc(start.index[0])
        orb = g.iloc[i0:i0 + n_or]
        if len(orb) < n_or:
            continue
        hi, lo = orb["high"].max(), orb["low"].min()
        after = g.iloc[i0 + n_or:]
        after = after[_london(after.index).hour < exit_london]
        for t, c in after["close"].items():
            dirn = 1 if c > hi else -1 if c < lo else 0
            if dirn:
                rows.append((t, dirn, lo if dirn == 1 else hi, np.nan, rr, _bars_until(g.index, t, exit_london)))
                break
    return _cap_per_day(rows, 1)


# ------------------------------------------------------------- S-016 Turtle System 2 (Dennis and Eckhardt)
def s016_turtle_s2(df: pd.DataFrame, entry_len: int = 55, n_len: int = 20, stop_n: float = 2.0, rr: float = 4.0,
                   max_bars: int = 480) -> pd.DataFrame:
    """Turtle System 2 (Curtis Faith's published rules): enter on a break of the 55-bar high (low),
    stop 2N away where N is the 20-bar ATR. Adaptation: H4 bars, no pyramiding, and because the engine
    has no trailing exit the 20-bar exit is approximated by a 4R target and a 20-day time stop."""
    h = resample(df, "4h")
    n = atr(h, n_len)
    hi = h["high"].rolling(entry_len).max().shift(1)
    lo = h["low"].rolling(entry_len).min().shift(1)
    long = (h["close"] > hi) & (h["close"].shift(1) <= hi.shift(1))
    short = (h["close"] < lo) & (h["close"].shift(1) >= lo.shift(1))
    rows = []
    for t in h.index[(long | short) & n.notna()]:
        tm = _htf_to_m15(t)
        if tm not in df.index:
            continue
        d = 1 if long.loc[t] else -1
        rows.append((tm, d, h.at[t, "close"] - d * stop_n * n.loc[t], np.nan, rr, max_bars))
    return _cap_per_day(rows, 1)


# ------------------------------------------------------------- F-01 Paul Tudor Jones 200-day filter
def ptj_200d_filter(df: pd.DataFrame, signals: pd.DataFrame, length: int = 200) -> pd.DataFrame:
    """Keep longs only above the 200-day SMA of daily closes and shorts only below it (Jones: 'get out
    of anything that falls below the 200-day moving average'). Uses completed days only."""
    if signals.empty:
        return signals
    sma = _daily(df)["close"].rolling(length).mean()
    level = _align(sma, df.index).reindex(signals.index)
    px = df["close"].reindex(signals.index)
    keep = ((signals["direction"] == 1) & (px > level)) | ((signals["direction"] == -1) & (px < level))
    return signals[keep.fillna(False)]


def with_ptj(fn):
    def wrapped(df: pd.DataFrame, **kw) -> pd.DataFrame:
        return ptj_200d_filter(df, fn(df, **kw))
    wrapped.__wrapped__ = fn
    wrapped.__name__ = fn.__name__ + "_ptj"
    import inspect
    wrapped.__signature__ = inspect.signature(fn)
    return wrapped


CHAMPION_STRATEGIES = {
    "S-011": s011_volatility_breakout,
    "S-012": s012_prior_day_breakout,
    "S-013": s013_turtle_soup,
    "S-014": s014_holy_grail,
    "S-015": s015_nr7_orb,
    "S-016": s016_turtle_s2,
    "S-014+PTJ": with_ptj(s014_holy_grail),
    "S-016+PTJ": with_ptj(s016_turtle_s2),
}
