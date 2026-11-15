"""Market data loading.

Accepted sources (real data only for validation; synthetic data only in tests):
  * MT5 "Export Bars" files: tab-separated <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> <TICKVOL> <VOL> <SPREAD>
  * Generic CSV with columns time/open/high/low/close (and optional spread)

MT5 times are broker server time. Pass the server's UTC offset; the loader
converts to UTC so session logic is correct across daylight-saving changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DataReport:
    source: str
    bars: int
    start: str
    end: str
    timeframe_minutes: int
    gaps_over_3x: int
    duplicate_times: int
    bad_ohlc: int


def load_mt5_bars(path: str | Path, server_utc_offset_hours: float = 2.0, point: float = 0.01) -> pd.DataFrame:
    """Load an MT5 Export Bars file. SPREAD (in points) is converted to price units via `point`."""
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [c.strip("<>").lower() for c in df.columns]
    if "date" in df.columns and "time" in df.columns:
        ts = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), format="mixed")
    else:
        ts = pd.to_datetime(df[df.columns[0]], format="mixed")
    out = pd.DataFrame(
        {
            "open": df["open"].astype(float),
            "high": df["high"].astype(float),
            "low": df["low"].astype(float),
            "close": df["close"].astype(float),
        }
    )
    out["spread"] = df["spread"].astype(float) * point if "spread" in df.columns else np.nan
    out.index = (ts - pd.Timedelta(hours=server_utc_offset_hours)).dt.tz_localize("UTC")
    out.index.name = "time"
    return out.sort_index()


def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    t = cols.get("time") or cols.get("datetime") or cols.get("date")
    out = pd.DataFrame({k: df[cols[k]].astype(float) for k in ("open", "high", "low", "close")})
    out["spread"] = df[cols["spread"]].astype(float) if "spread" in cols else np.nan
    idx = pd.to_datetime(df[t], utc=True, format="mixed")
    out.index = idx
    out.index.name = "time"
    return out.sort_index()


def quality_report(df: pd.DataFrame, source: str) -> DataReport:
    diffs = df.index.to_series().diff().dropna()
    tf = int(diffs.mode().iloc[0].total_seconds() // 60) if len(diffs) else 0
    # Weekend closes are expected; count gaps > 3 bars that do not span a weekend.
    big = diffs[diffs > pd.Timedelta(minutes=3 * tf)] if tf else diffs.iloc[:0]
    weekday_gaps = [i for i in big.index if not (big[i] >= pd.Timedelta(hours=40))]
    bad = int(((df["high"] < df[["open", "close"]].max(axis=1)) | (df["low"] > df[["open", "close"]].min(axis=1))).sum())
    return DataReport(
        source=source,
        bars=len(df),
        start=str(df.index.min()),
        end=str(df.index.max()),
        timeframe_minutes=tf,
        gaps_over_3x=len(weekday_gaps),
        duplicate_times=int(df.index.duplicated().sum()),
        bad_ohlc=bad,
    )


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample to a higher timeframe. Bars are labelled by their CLOSE time so a bar is
    only usable once it has finished (prevents look-ahead when joined to lower timeframes)."""
    agg = df.resample(rule, label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "spread": "mean"}
    )
    return agg.dropna(subset=["open"])


def synthetic_gold(n_bars: int = 20000, seed: int = 1, start: str = "2024-01-01", freq: str = "15min") -> pd.DataFrame:
    """SYNTHETIC random-walk bars for tests only. Never use for validation."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n_bars * 2, freq=freq, tz="UTC")
    idx = idx[idx.dayofweek < 5][:n_bars]
    rets = rng.normal(0, 0.0012, len(idx))
    close = 2000 * np.exp(np.cumsum(rets))
    open_ = np.r_[close[0], close[:-1]]
    wick = np.abs(rng.normal(0, 0.0008, len(idx))) * close
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "spread": 0.3}, index=pd.DatetimeIndex(idx, name="time"))
