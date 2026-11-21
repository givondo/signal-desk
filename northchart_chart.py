"""Price history for the Northchart chart: any timeframe, from Yahoo Finance.

* Timeframes 1m, 5m, 15m, 30m, 1h, 4h, 1D, 1W. Yahoo has no 4h bars, so 4h is built
  from 1h bars on UTC boundaries (00, 04, 08, ...).
* Futures proxies (GC=F, SI=F, CL=F) are shifted by the spot-futures basis so the bars
  line up with the desk's spot levels; the response says so. The shift is skipped when
  the gap is over 3 % (a roll or bad print), as in the desk's own 15m charts.
* Results are cached per instrument and timeframe for 30 seconds on intraday frames and
  five minutes on daily and weekly ones.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

TIMEFRAMES = {
    # key: (Yahoo interval, Yahoo range, cache seconds, KLineChart period)
    "1m": ("1m", "5d", 30, {"type": "minute", "span": 1}),
    "5m": ("5m", "30d", 30, {"type": "minute", "span": 5}),
    "15m": ("15m", "60d", 30, {"type": "minute", "span": 15}),
    "30m": ("30m", "60d", 30, {"type": "minute", "span": 30}),
    "1h": ("60m", "180d", 60, {"type": "hour", "span": 1}),
    "4h": ("60m", "730d", 120, {"type": "hour", "span": 4}),
    "1D": ("1d", "10y", 300, {"type": "day", "span": 1}),
    "1W": ("1wk", "max", 300, {"type": "week", "span": 1}),
}
MAX_BARS = 5000


class Bars:
    def __init__(self, symbols_fn, latest_fn, fetch=None):
        self.symbols, self.latest = symbols_fn, latest_fn
        self.fetch = fetch or self._fetch
        self.cache = {}
        self.lock = threading.Lock()

    @staticmethod
    def _fetch(ticker, interval, rng):
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}"
               f"?interval={interval}&range={rng}&includePrePost=false")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Northchart"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    @staticmethod
    def _rows(data):
        res = data["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        ts = res.get("timestamp") or []
        out = []
        for i, t in enumerate(ts):
            o, h, lo, c = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close"))
            if None in (h, lo, c):
                continue
            v = (q.get("volume") or [0] * len(ts))[i] or 0
            out.append([int(t), o if o is not None else c, h, lo, c, v])
        return out

    @staticmethod
    def _four_hour(rows):
        out, cur = [], None
        for t, o, h, lo, c, v in rows:
            b = t - t % 14400
            if cur is None or cur[0] != b:
                if cur:
                    out.append(cur)
                cur = [b, o, h, lo, c, v]
            else:
                cur[2], cur[3], cur[4], cur[5] = max(cur[2], h), min(cur[3], lo), c, cur[5] + v
        if cur:
            out.append(cur)
        return out

    def get(self, sym, tf="15m"):
        syms = self.symbols()
        if sym not in syms:
            raise ValueError("unknown instrument")
        if tf not in TIMEFRAMES:
            raise ValueError("timeframe must be one of " + ", ".join(TIMEFRAMES))
        interval, rng, ttl, period = TIMEFRAMES[tf]
        meta = syms[sym]
        ticker = meta.get("candles") or meta.get("news_sym")
        if not ticker:
            raise ValueError("no price history source for this instrument")
        key = (sym, tf)
        with self.lock:
            hit = self.cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            rows = hit[1]
        else:
            rows = self._rows(self.fetch(ticker, interval, rng))
            if tf == "4h":
                rows = self._four_hour(rows)
            rows = rows[-MAX_BARS:]
            with self.lock:
                self.cache[key] = (time.time(), rows)
        dp = int(meta.get("dp", 2))
        st = self.latest(sym) or {}
        px = st.get("price")
        basis, source = 0.0, f"Yahoo Finance {ticker}"
        if ticker.endswith("=F") and isinstance(px, (int, float)) and rows and rows[-1][4]:
            b = px - rows[-1][4]
            if abs(b) / px < 0.03 and tf not in ("1D", "1W"):
                basis = round(b, dp)
                source += f" futures, shifted {basis:+.{dp}f} to spot" if basis else " futures"
            else:
                source += " futures"
        bars = [{"timestamp": t * 1000, "open": round(o + basis, dp), "high": round(h + basis, dp),
                 "low": round(lo + basis, dp), "close": round(c + basis, dp), "volume": v}
                for t, o, h, lo, c, v in rows]
        lv = st.get("levels") or {}
        levels = [{"key": k, "label": lbl, "value": lv.get(k)} for k, lbl in
                  (("entry", "Entry"), ("sl", "Stop"), ("tp1", "Target 1"), ("tp2", "Target 2"))
                  if isinstance(lv.get(k), (int, float))]
        return {"symbol": sym, "name": meta.get("name") or sym, "timeframe": tf, "period": period,
                "dp": dp, "source": source, "basis": basis, "bars": bars, "levels": levels,
                "direction": st.get("direction"), "grade": st.get("grade"), "price": px,
                "updated": int(time.time())}
