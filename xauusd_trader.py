"""
Signal Desk v4 - institutional-grade terminal backend
-----------------------------------------------------
Serves http://localhost:8899 (dashboard.html) and a JSON API:

  /api/signal?sym=XAUUSD|BTCUSD   full state: price, MTF matrix, confluence
                                  scores, entry models, reasons, macro, news,
                                  alerts, performance analytics
  /api/ask?sym=..&q=..            rule-based copilot answers from live state
  /api/tv_login (POST)            TradingView account session

Data: TradingView scanner (multi-timeframe technicals), Yahoo (macro complex,
15m candles, RSS news), optional Bloomberg Terminal via blpapi.
Educational tool - NOT financial advice.  Stdlib only.
"""

import base64
import json
import os
import re
import statistics
import sys
import threading
import time
import urllib.request
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Optional HTTP Basic auth: set SIGNALDESK_PASS (and optionally
# SIGNALDESK_USER, default "trader") to require login on every route.
# Unset = open, for localhost/Tailscale-only deployments.
AUTH_USER = os.environ.get("SIGNALDESK_USER", "trader")
AUTH_PASS = os.environ.get("SIGNALDESK_PASS")

PORT = int(os.environ.get("PORT", 8899))   # PaaS hosts inject PORT
POLL_SECONDS = 15
MACRO_EVERY = 4          # macro/candles every 60s
NEWS_EVERY = 20          # news every 5 min
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Persist state to a mounted volume when provided (Railway/Fly), else local dir.
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    DATA_DIR = BASE_DIR

# ---- asset-class macro profiles (scoring weights + display impact + why) ----
# w      : signed weights for the 3 scored drivers (dxy / us10y / vix)
# impact : effect of each driver RISING (+1 bull / -1 bear / 0 context)
# why    : one-line reason each driver matters for this asset class
CLASS_MACRO = {
    "metal": {
        "w": {"dxy": -0.40, "us10y": -0.35, "vix": 0.25},
        "impact": {"dxy": -1, "us10y": -1, "us2y": -1, "vix": 1, "oil": 1,
                   "silver": 1, "copper": 0, "spx": 0, "ndq": 0, "gcf": 1},
        "why": {"dxy": "priced in dollars, so a stronger dollar makes it costlier worldwide",
                "us10y": "pays no yield, so rising rates pull money into bonds instead",
                "us2y": "tighter Fed policy raises the cost of holding a zero-yield asset",
                "vix": "a classic safe haven - fear tends to lift it",
                "oil": "pricey oil stokes inflation, and metals hedge inflation",
                "silver": "the metals complex moves together - agreement confirms the move",
                "copper": "growth signal - weak direct link",
                "spx": "equity appetite competes for the same flows",
                "ndq": "risk-appetite context", "gcf": "gold-futures flow reference"},
    },
    "crypto": {
        "w": {"dxy": -0.35, "us10y": -0.25, "vix": -0.40},
        "impact": {"dxy": -1, "us10y": -1, "us2y": -1, "vix": -1, "oil": 0,
                   "silver": 0, "copper": 0, "spx": 1, "ndq": 1, "gcf": 0},
        "why": {"dxy": "trades like a dollar-priced risk asset - dollar strength drains it",
                "us10y": "higher yields make safe income attractive and starve risk assets",
                "us2y": "Fed tightening expectations hit crypto liquidity first",
                "vix": "sold as a risk asset in fear episodes, not bought as a haven",
                "oil": "inflation context - weak direct link", "silver": "metals context only",
                "copper": "global-growth pulse - context", "spx": "follows broad equity risk appetite",
                "ndq": "correlates strongest with high-beta tech",
                "gcf": "gold bid while crypto falls = money choosing the old haven"},
    },
    "anti_usd": {   # EUR GBP AUD NZD - the pair RISES when the dollar FALLS
        "w": {"dxy": -0.55, "us10y": -0.15, "vix": -0.15},
        "impact": {"dxy": -1, "us10y": -1, "us2y": -1, "vix": -1, "oil": 0,
                   "silver": 0, "copper": 0, "spx": 1, "ndq": 0, "gcf": 0},
        "why": {"dxy": "the pair mirrors the dollar - a stronger USD pushes it down",
                "us10y": "higher US yields pull capital into the dollar, away from this currency",
                "us2y": "US rate-hike expectations strengthen the dollar leg",
                "vix": "risk-off flows into the dollar hurt higher-beta currencies",
                "oil": "commodity context", "silver": "context only",
                "copper": "growth-currency context", "spx": "risk-on lifts non-dollar currencies",
                "ndq": "risk context", "gcf": "haven-flow context"},
    },
    "pro_usd": {    # USDJPY USDCAD - the pair RISES when the dollar RISES
        "w": {"dxy": 0.50, "us10y": 0.35, "vix": -0.10},
        "impact": {"dxy": 1, "us10y": 1, "us2y": 1, "vix": -1, "oil": 0,
                   "silver": 0, "copper": 0, "spx": 1, "ndq": 0, "gcf": 0},
        "why": {"dxy": "USD is the base currency, so dollar strength pushes the pair up",
                "us10y": "a wider US yield advantage pulls money into the dollar leg",
                "us2y": "US rate-hike expectations lift the dollar",
                "vix": "risk-off often bids the haven leg, capping the pair",
                "oil": "petro-currency context (matters most for USD/CAD)", "silver": "context only",
                "copper": "growth context", "spx": "risk-on supports carry into the pair",
                "ndq": "risk context", "gcf": "haven-flow context"},
    },
    "index": {
        "w": {"dxy": -0.10, "us10y": -0.45, "vix": -0.45},
        "impact": {"dxy": 0, "us10y": -1, "us2y": -1, "vix": -1, "oil": 0,
                   "silver": 0, "copper": 1, "spx": 1, "ndq": 1, "gcf": -1},
        "why": {"dxy": "modest link - a strong dollar can trim overseas earnings",
                "us10y": "higher discount rates compress equity valuations",
                "us2y": "Fed-tightening expectations weigh on stocks",
                "vix": "this is the fear gauge's mirror - spikes mean selling",
                "oil": "energy-cost context", "silver": "context only",
                "copper": "growth confirmation for cyclical earnings",
                "spx": "the broad tape - moves together", "ndq": "tech-leadership context",
                "gcf": "safe-haven rotation away from stocks"},
    },
    "energy": {
        "w": {"dxy": -0.30, "us10y": -0.05, "vix": -0.30},
        "impact": {"dxy": -1, "us10y": 0, "us2y": 0, "vix": -1, "oil": 0,
                   "silver": 0, "copper": 1, "spx": 1, "ndq": 0, "gcf": 0},
        "why": {"dxy": "dollar-priced - a stronger dollar makes it costlier for foreign buyers",
                "us10y": "weak direct link - matters mainly via growth expectations",
                "us2y": "policy context - weak direct link",
                "vix": "risk-off usually means demand fears - UNLESS it's a supply shock, then oil rises with VIX",
                "oil": "this is the instrument itself", "silver": "context only",
                "copper": "copper rising = factories busy = more oil demand",
                "spx": "equity strength signals demand-side confidence", "ndq": "risk context",
                "gcf": "gold and oil rising together can flag a geopolitical shock"},
    },
}


def _pf(name):
    return os.path.join(DATA_DIR, name)


# roster: cls -> macro profile; dp = price decimals; rt = real-time scanner
# data (oil is delayed); word/grp used for UI text and grouping. tv_chart == tv.
SYMBOLS = {
    "XAUUSD": {"tv": "OANDA:XAUUSD", "name": "GOLD SPOT", "word": "gold",
               "cls": "metal", "grp": "Metals", "dp": 2, "per_point": 100,
               "size_note": "1 lot = 100oz = $100/pt", "spread_est": 0.30,
               "candles": "GC=F", "news_sym": "GC=F", "session_filter": True,
               "rt": True, "pred_file": _pf("predictions.json")},
    "XAGUSD": {"tv": "TVC:SILVER", "name": "SILVER SPOT", "word": "silver",
               "cls": "metal", "grp": "Metals", "dp": 3, "per_point": 5000,
               "size_note": "1 lot = 5000oz = $5000/pt", "spread_est": 0.03,
               "candles": "SI=F", "news_sym": "SI=F", "session_filter": True,
               "rt": True, "pred_file": _pf("predictions_xag.json")},
    "BTCUSD": {"tv": "COINBASE:BTCUSD", "name": "BITCOIN", "word": "bitcoin",
               "cls": "crypto", "grp": "Crypto", "dp": 2, "per_point": 1,
               "size_note": "1 lot = 1 BTC = $1/pt", "spread_est": 15.0,
               "candles": "BTC-USD", "news_sym": "BTC-USD", "session_filter": False,
               "rt": True, "pred_file": _pf("predictions_btc.json")},
    "ETHUSD": {"tv": "COINBASE:ETHUSD", "name": "ETHEREUM", "word": "ether",
               "cls": "crypto", "grp": "Crypto", "dp": 2, "per_point": 1,
               "size_note": "1 lot = 1 ETH = $1/pt", "spread_est": 1.2,
               "candles": "ETH-USD", "news_sym": "ETH-USD", "session_filter": False,
               "rt": True, "pred_file": _pf("predictions_eth.json")},
    "SOLUSD": {"tv": "COINBASE:SOLUSD", "name": "SOLANA", "word": "solana",
               "cls": "crypto", "grp": "Crypto", "dp": 2, "per_point": 1,
               "size_note": "1 lot = 1 SOL = $1/pt", "spread_est": 0.05,
               "candles": "SOL-USD", "news_sym": "SOL-USD", "session_filter": False,
               "rt": True, "pred_file": _pf("predictions_sol.json")},
    "XRPUSD": {"tv": "COINBASE:XRPUSD", "name": "XRP", "word": "XRP",
               "cls": "crypto", "grp": "Crypto", "dp": 4, "per_point": 1,
               "size_note": "1 lot = 1 XRP = $1/pt", "spread_est": 0.001,
               "candles": "XRP-USD", "news_sym": "XRP-USD", "session_filter": False,
               "rt": True, "pred_file": _pf("predictions_xrp.json")},
    "EURUSD": {"tv": "OANDA:EURUSD", "name": "EUR / USD", "word": "the euro",
               "cls": "anti_usd", "grp": "FX Majors", "dp": 5, "per_point": 100000,
               "size_note": "1 lot = 100k = $10/pip", "spread_est": 0.00012,
               "candles": "EURUSD=X", "news_sym": "EURUSD=X", "session_filter": True,
               "rt": True, "pred_file": _pf("predictions_eur.json")},
    "GBPUSD": {"tv": "OANDA:GBPUSD", "name": "GBP / USD", "word": "the pound",
               "cls": "anti_usd", "grp": "FX Majors", "dp": 5, "per_point": 100000,
               "size_note": "1 lot = 100k = $10/pip", "spread_est": 0.00015,
               "candles": "GBPUSD=X", "news_sym": "GBPUSD=X", "session_filter": True,
               "rt": True, "pred_file": _pf("predictions_gbp.json")},
    "AUDUSD": {"tv": "OANDA:AUDUSD", "name": "AUD / USD", "word": "the aussie",
               "cls": "anti_usd", "grp": "FX Majors", "dp": 5, "per_point": 100000,
               "size_note": "1 lot = 100k = $10/pip", "spread_est": 0.00015,
               "candles": "AUDUSD=X", "news_sym": "AUDUSD=X", "session_filter": True,
               "rt": True, "pred_file": _pf("predictions_aud.json")},
    "NZDUSD": {"tv": "OANDA:NZDUSD", "name": "NZD / USD", "word": "the kiwi",
               "cls": "anti_usd", "grp": "FX Majors", "dp": 5, "per_point": 100000,
               "size_note": "1 lot = 100k = $10/pip", "spread_est": 0.0002,
               "candles": "NZDUSD=X", "news_sym": "NZDUSD=X", "session_filter": True,
               "rt": True, "pred_file": _pf("predictions_nzd.json")},
    "USDJPY": {"tv": "OANDA:USDJPY", "name": "USD / JPY", "word": "dollar-yen",
               "cls": "pro_usd", "grp": "FX Majors", "dp": 3, "per_point": None,
               "per_point_dynamic": True, "size_note": "1 lot = 100k, pip ~ 1000/px",
               "spread_est": 0.015, "candles": "USDJPY=X", "news_sym": "USDJPY=X",
               "session_filter": True, "rt": True, "pred_file": _pf("predictions_jpy.json")},
    "USDCAD": {"tv": "OANDA:USDCAD", "name": "USD / CAD", "word": "dollar-loonie",
               "cls": "pro_usd", "grp": "FX Majors", "dp": 5, "per_point": 100000,
               "size_note": "1 lot = 100k = $10/pip", "spread_est": 0.0002,
               "candles": "USDCAD=X", "news_sym": "USDCAD=X", "session_filter": True,
               "rt": True, "pred_file": _pf("predictions_cad.json")},
    "US30": {"tv": "OANDA:US30USD", "name": "DOW 30", "word": "the Dow",
             "cls": "index", "grp": "Indices", "dp": 1, "per_point": 1,
             "size_note": "$1 / point (CFD)", "spread_est": 2.0,
             "candles": "^DJI", "news_sym": "^DJI", "session_filter": True,
             "rt": True, "pred_file": _pf("predictions_us30.json")},
    "NAS100": {"tv": "NASDAQ:NDX", "name": "NASDAQ 100", "word": "the Nasdaq",
               "cls": "index", "grp": "Indices", "dp": 1, "per_point": 1,
               "size_note": "$1 / point (CFD)", "spread_est": 1.5,
               "candles": "^NDX", "news_sym": "^NDX", "session_filter": True,
               "rt": True, "pred_file": _pf("predictions_nas.json")},
    "USOIL": {"tv": "NYMEX:CL1!", "name": "WTI CRUDE", "word": "oil",
              "cls": "energy", "grp": "Energy", "dp": 2, "per_point": 1000,
              "size_note": "1 lot = 1000 bbl = $1000/pt", "spread_est": 0.03,
              "candles": "CL=F", "news_sym": "CL=F", "session_filter": True,
              "rt": False, "pred_file": _pf("predictions_oil.json")},
}
for _s, _c in SYMBOLS.items():
    _c["tv_chart"] = _c["tv"]
    _c["macro_w"] = CLASS_MACRO[_c["cls"]]["w"]

# multi-timeframe matrix: TradingView scanner suffixes (1m is the floor)
TFS = [("1m", "|1"), ("5m", "|5"), ("15m", "|15"), ("30m", "|30"),
       ("1h", "|60"), ("4h", "|240"), ("1D", "")]
TF_FIELDS = ["Recommend.All", "RSI", "ADX", "MACD.macd", "MACD.signal",
             "EMA20", "EMA50", "Mom"]
BASE_FIELDS = ["close", "change", "high", "low", "volume",
               "average_volume_10d_calc", "ATR", "ATR|60", "ATR|15",
               "Stoch.K|15", "EMA200|60", "EMA200",
               "Pivot.M.Classic.S1", "Pivot.M.Classic.S2",
               "Pivot.M.Classic.R1", "Pivot.M.Classic.R2",
               "Pivot.M.Classic.Middle"]
TV_FIELDS = BASE_FIELDS + [f + suf for _, suf in TFS for f in TF_FIELDS]

MACRO_SYMS = [
    ("dxy",   "DX-Y.NYB", "DXY DOLLAR IDX", "%"),
    ("us10y", "^TNX",     "US 10Y YIELD",   "bps"),
    ("us2y",  "2YY=F",    "US 2Y YIELD",    "bps"),
    ("vix",   "^VIX",     "VIX",            "%"),
    ("oil",   "CL=F",     "WTI CRUDE",      "%"),
    ("silver","SI=F",     "SILVER",         "%"),
    ("copper","HG=F",     "COPPER",         "%"),
    ("spx",   "ES=F",     "S&P500 FUT",     "%"),
    ("ndq",   "NQ=F",     "NASDAQ FUT",     "%"),
    ("gcf",   "GC=F",     "GOLD FUT GC",    "%"),
]
# per-symbol qualitative impact, derived from each symbol's asset-class profile
MACRO_IMPACT = {s: CLASS_MACRO[c["cls"]]["impact"] for s, c in SYMBOLS.items()}
MACRO_EXPLAIN = {
    "dxy":   "Strength of the US dollar against major currencies",
    "us10y": "Interest the US government pays to borrow for 10 years",
    "us2y":  "2-year rate - tracks Fed policy expectations most closely",
    "vix":   "Wall Street's fear gauge, from S&P option prices",
    "oil":   "WTI crude - the economy's energy cost input",
    "silver":"Gold's high-beta sibling metal",
    "copper":"Industrial metal - global growth proxy",
    "spx":   "US equity futures - broad risk appetite",
    "ndq":   "Tech-heavy equity futures - high-beta risk appetite",
    "gcf":   "Gold futures - safe-haven flow reference",
}

# per-symbol WHY text, derived from each symbol's asset-class profile
MACRO_ASSET_WHY = {s: CLASS_MACRO[c["cls"]]["why"] for s, c in SYMBOLS.items()}

EXPIRY_S = 6 * 3600
COOLDOWN_S = 300
AUTH_FILE = os.path.join(DATA_DIR, "tv_auth.json")
DASH_FILE = os.path.join(BASE_DIR, "dashboard.html")   # read-only shipped asset

state_lock = threading.Lock()
latest = {s: {"status": "starting"} for s in SYMBOLS}
price_history = {s: deque(maxlen=480) for s in SYMBOLS}
macro_cache = {"source": None, "items": {}, "ts": None}
candle_cache = {s: {} for s in SYMBOLS}
news_cache = {s: [] for s in SYMBOLS}
alerts = {s: deque(maxlen=40) for s in SYMBOLS}
prev_state = {s: {} for s in SYMBOLS}
tv_auth = {"sessionid": None, "sessionid_sign": None, "username": None}


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def load_auth():
    try:
        with open(AUTH_FILE, "r") as f:
            tv_auth.update(json.load(f))
    except Exception:
        pass


def save_auth():
    try:
        with open(AUTH_FILE, "w") as f:
            json.dump(tv_auth, f)
    except Exception:
        pass


def tv_cookie_header():
    if not tv_auth.get("sessionid"):
        return None
    c = f"sessionid={tv_auth['sessionid']}"
    if tv_auth.get("sessionid_sign"):
        c += f"; sessionid_sign={tv_auth['sessionid_sign']}"
    return c


# ---------------------------------------------------------------- feeds

def http_get(url, cookie=None, timeout=10):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def http_json(url, cookie=None):
    return json.loads(http_get(url, cookie))


def fetch_tv(tv_symbol):
    qs = urllib.parse.urlencode({"symbol": tv_symbol,
                                 "fields": ",".join(TV_FIELDS),
                                 "no_404": "true"})
    return http_json(f"https://scanner.tradingview.com/symbol?{qs}",
                     cookie=tv_cookie_header())


def fetch_yahoo_quote(sym):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(sym)}?interval=5m&range=1d")
    meta = http_json(url)["chart"]["result"][0]["meta"]
    last, prev = meta.get("regularMarketPrice"), meta.get("chartPreviousClose")
    if last is None or not prev:
        return None
    return {"last": last, "chg_pct": round((last / prev - 1) * 100, 2),
            "chg_net": round(last - prev, 4)}


def fetch_bloomberg_macro():
    try:
        import blpapi
    except ImportError:
        return None
    try:
        opts = blpapi.SessionOptions()
        opts.setServerHost("localhost")
        opts.setServerPort(8194)
        sess = blpapi.Session(opts)
        if not sess.start() or not sess.openService("//blp/refdata"):
            return None
        svc = sess.getService("//blp/refdata")
        req = svc.createRequest("ReferenceDataRequest")
        tickers = {"DXY Index": "dxy", "USGG10YR Index": "us10y",
                   "USGG2YR Index": "us2y", "VIX Index": "vix",
                   "CL1 Comdty": "oil", "SI1 Comdty": "silver",
                   "HG1 Comdty": "copper", "ES1 Index": "spx",
                   "NQ1 Index": "ndq", "GC1 Comdty": "gcf"}
        for t in tickers:
            req.getElement("securities").appendValue(t)
        for f in ("PX_LAST", "CHG_PCT_1D", "CHG_NET_1D"):
            req.getElement("fields").appendValue(f)
        sess.sendRequest(req)
        out = {}
        while True:
            ev = sess.nextEvent(3000)
            for msg in ev:
                if not msg.hasElement("securityData"):
                    continue
                sd = msg.getElement("securityData")
                for i in range(sd.numValues()):
                    row = sd.getValueAsElement(i)
                    key = tickers.get(row.getElementAsString("security"))
                    fd = row.getElement("fieldData")
                    if key and fd.hasElement("PX_LAST"):
                        out[key] = {
                            "last": fd.getElementAsFloat("PX_LAST"),
                            "chg_pct": (fd.getElementAsFloat("CHG_PCT_1D")
                                        if fd.hasElement("CHG_PCT_1D") else 0.0),
                            "chg_net": (fd.getElementAsFloat("CHG_NET_1D")
                                        if fd.hasElement("CHG_NET_1D") else 0.0)}
            if ev.eventType() == blpapi.Event.RESPONSE:
                break
        sess.stop()
        return out or None
    except Exception:
        return None


def refresh_macro():
    items, source = fetch_bloomberg_macro(), "BLOOMBERG"
    if not items:
        source = "WEB FEEDS"
        items = {}
        for key, ysym, _, _ in MACRO_SYMS:
            try:
                q = fetch_yahoo_quote(ysym)
                if q:
                    items[key] = q
            except Exception:
                pass
    for k in ("us10y", "us2y"):
        if k in items:
            items[k]["chg_bps"] = round(items[k]["chg_net"] * 100, 1)
    macro_cache.update({"source": source, "items": items,
                        "ts": time.strftime("%H:%M:%S")})


def macro_score_for(weights):
    items = macro_cache.get("items", {})
    score, contribs = 0.0, {}
    if "dxy" in items and "dxy" in weights:
        c = clamp(items["dxy"]["chg_pct"] / 0.5) * weights["dxy"]
        contribs["dxy"] = round(c, 3)
        score += c
    if "us10y" in items and "us10y" in weights:
        c = clamp(items["us10y"].get("chg_bps", 0) / 8) * weights["us10y"]
        contribs["us10y"] = round(c, 3)
        score += c
    if "vix" in items and "vix" in weights:
        c = clamp(items["vix"]["chg_pct"] / 10) * weights["vix"]
        contribs["vix"] = round(c, 3)
        score += c
    return round(clamp(score), 3), contribs


def refresh_candles(sym):
    """15m candles -> latest fractal swing high/low as offsets from close."""
    data = http_json("https://query1.finance.yahoo.com/v8/finance/chart/"
                     f"{urllib.parse.quote(SYMBOLS[sym]['candles'])}"
                     "?interval=15m&range=2d")
    q = data["chart"]["result"][0]["indicators"]["quote"][0]
    hs, ls, cs = [], [], []
    for h, l, c in zip(q["high"], q["low"], q["close"]):
        if h is not None and l is not None and c is not None:
            hs.append(h)
            ls.append(l)
            cs.append(c)
    if len(cs) < 10:
        return
    ref = cs[-1]
    swing_hi = swing_lo = None
    for i in range(len(hs) - 3, 1, -1):
        if swing_hi is None and hs[i] == max(hs[i - 2:i + 3]):
            swing_hi = hs[i]
        if swing_lo is None and ls[i] == min(ls[i - 2:i + 3]):
            swing_lo = ls[i]
        if swing_hi is not None and swing_lo is not None:
            break
    candle_cache[sym].update({
        "hi_off": round(swing_hi - ref, 6) if swing_hi is not None else None,
        "lo_off": round(ref - swing_lo, 6) if swing_lo is not None else None,
        "ts": time.strftime("%H:%M:%S")})


# ---------------------------------------------------------------- news

NEWS_CATS = [
    ("FED", ["fed", "fomc", "powell", "rate decision", "federal reserve"]),
    ("INFLATION", ["inflation", "cpi", "ppi", "pce", "price index"]),
    ("GEOPOLITICS", ["war", "geopolit", "sanction", "conflict", "military",
                     "tension", "attack"]),
    ("EMPLOYMENT", ["jobs", "payroll", "unemployment", "labor", "jobless"]),
    ("CENTRAL BANKS", ["ecb", "boj", "boe", "central bank", "pboc", "snb"]),
    ("COMMODITIES", ["gold", "silver", "oil", "commodit", "metal", "mining"]),
]
BULL_KW = {"XAUUSD": ["rate cut", "dovish", "safe haven", "haven demand",
                      "dollar weak", "yields fall", "yields slip", "tension",
                      "war", "inflation fear", "record high", "rally",
                      "surge", "gain"],
           "BTCUSD": ["etf inflow", "adoption", "rally", "surge", "rate cut",
                      "dovish", "dollar weak", "record high", "gain",
                      "institutional buy"]}
BEAR_KW = {"XAUUSD": ["rate hike", "hawkish", "dollar strength", "dollar rise",
                      "yields rise", "yields jump", "profit taking", "fall",
                      "drop", "slump", "sell-off", "selloff"],
           "BTCUSD": ["crackdown", "ban", "hack", "outflow", "rate hike",
                      "hawkish", "risk-off", "fall", "drop", "slump",
                      "sell-off", "selloff", "liquidation"]}


def fetch_news(sym):
    url = ("https://feeds.finance.yahoo.com/rss/2.0/headline?s="
           f"{urllib.parse.quote(SYMBOLS[sym]['news_sym'])}&region=US&lang=en-US")
    xml = http_get(url, timeout=12)
    out = []
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        item = m.group(1)
        t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.S)
        lk = re.search(r"<link>(.*?)</link>", item, re.S)
        dt = re.search(r"<pubDate>(.*?)</pubDate>", item, re.S)
        if not t:
            continue
        title = t.group(1).strip()
        low = title.lower()
        cat = next((c for c, kws in NEWS_CATS
                    if any(k in low for k in kws)), "MARKETS")
        gen_bull = ["rally", "surge", "gain", "record high", "beat", "rise",
                    "climb", "rate cut", "dovish"]
        gen_bear = ["fall", "drop", "slump", "sell-off", "selloff", "miss",
                    "decline", "rate hike", "hawkish"]
        bull = sum(1 for k in BULL_KW.get(sym, gen_bull) if k in low)
        bear = sum(1 for k in BEAR_KW.get(sym, gen_bear) if k in low)
        impact = "BULLISH" if bull > bear else ("BEARISH" if bear > bull
                                                else "NEUTRAL")
        conf = "MED" if abs(bull - bear) >= 2 else ("LOW" if bull or bear
                                                    else "-")
        out.append({"title": title[:160],
                    "link": (lk.group(1).strip() if lk else ""),
                    "date": (dt.group(1).strip()[:22] if dt else ""),
                    "cat": cat, "impact": impact, "conf": conf})
        if len(out) >= 12:
            break
    if out:
        news_cache[sym] = out


# ---------------------------------------------------------------- TV account

def tv_login_password(username, password):
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    try:
        opener.open(urllib.request.Request(
            "https://www.tradingview.com/", headers={"User-Agent": ua}),
            timeout=15).read()
    except Exception:
        pass
    data = urllib.parse.urlencode({"username": username, "password": password,
                                   "remember": "on"}).encode()
    req = urllib.request.Request(
        "https://www.tradingview.com/accounts/signin/", data=data,
        headers={"User-Agent": ua, "Accept": "application/json",
                 "Referer": "https://www.tradingview.com/",
                 "Origin": "https://www.tradingview.com",
                 "X-Requested-With": "XMLHttpRequest"})
    try:
        with opener.open(req, timeout=15) as r:
            body = json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": f"signin request failed: {e}"}
    if body.get("error"):
        return {"ok": False, "error": str(body["error"])}
    sid = next((c.value for c in jar if c.name == "sessionid"), None)
    sign = next((c.value for c in jar if c.name == "sessionid_sign"), None)
    user = (body.get("user") or {}).get("username") or username
    if not sid:
        return {"ok": False, "error": "no session cookie returned (captcha or "
                "2FA) - use the session cookie method"}
    return {"ok": True, "sessionid": sid, "sessionid_sign": sign,
            "username": user}


def tv_validate_cookie(sid, sign=None):
    cookie = f"sessionid={sid}"
    if sign:
        cookie += f"; sessionid_sign={sign}"
    try:
        html = http_get("https://www.tradingview.com/", cookie=cookie,
                        timeout=15)
    except Exception:
        return None
    if '"is_authenticated":true' in html or '"is_authenticated": true' in html:
        m = re.search(r'"username"\s*:\s*"([^"]+)"', html)
        return m.group(1) if m else "(authenticated)"
    return None


# ---------------------------------------------------------------- model

def cell(v, kind):
    """Heatmap cell state: 1 bull / -1 bear / 0 neutral, plus display value."""
    if v is None:
        return {"s": 0, "v": None}
    return {"s": kind(v), "v": round(v, 1) if isinstance(v, float) else v}


def build_matrix(d):
    rows = []
    for label, suf in TFS:
        g = lambda f: d.get(f + suf)
        rec, rsi, adx = g("Recommend.All"), g("RSI"), g("ADX")
        macd = (g("MACD.macd") - g("MACD.signal")) \
            if g("MACD.macd") is not None and g("MACD.signal") is not None else None
        e20, e50, mom, px = g("EMA20"), g("EMA50"), g("Mom"), d.get("close")
        trend = None
        if e20 and e50 and px:
            trend = 1 if (e20 > e50 and px > e20) else \
                (-1 if (e20 < e50 and px < e20) else 0)
        ema = None
        if e20 and e50:
            ema = 1 if e20 > e50 else -1
        rows.append({
            "tf": label,
            "trend": {"s": trend if trend is not None else 0, "v": None},
            "mom": cell(mom, lambda v: 1 if v > 0 else (-1 if v < 0 else 0)),
            "rsi": cell(rsi, lambda v: 1 if v > 55 else (-1 if v < 45 else 0)),
            "macd": cell(macd, lambda v: 1 if v > 0 else (-1 if v < 0 else 0)),
            "adx": cell(adx, lambda v: 1 if v >= 25 else (0 if v >= 18 else -1)),
            "ema": {"s": ema if ema is not None else 0, "v": None},
            "rating": cell(rec, lambda v: 1 if v > 0.1 else (-1 if v < -0.1 else 0)),
        })
    return rows


def build_scores(sym, d, matrix, macro, session, regime, structure_s, conf):
    W = {"1m": 0.5, "5m": 0.8, "15m": 1.2, "30m": 1.0, "1h": 1.5,
         "4h": 1.2, "1D": 0.8}
    tw = sum(W.values())
    trend_s = sum(r["trend"]["s"] * W[r["tf"]] for r in matrix) / tw * 100
    mom_s = sum((r["macd"]["s"] + r["mom"]["s"]) / 2 * W[r["tf"]]
                for r in matrix) / tw * 100
    vol, avg_vol = d.get("volume"), d.get("average_volume_10d_calc")
    volu_s = 0.0
    if vol and avg_vol:
        ratio = vol / avg_vol
        volu_s = clamp((ratio - 1) * 1.2) * 100 * (1 if trend_s >= 0 else -1)
    vola_s = {"LOW": 20, "NORMAL": 50, "HIGH": -20, "EXTREME": -60}.get(
        regime.get("vol", ""), 0)
    liq_s = {"high": 60, "normal": 20, "low": -50}.get(session.get("liq"), 0)
    # flow proxy: 1h+4h weighted TV rating = where systematic flow points
    rec60 = d.get("Recommend.All|60") or 0
    rec240 = d.get("Recommend.All|240") or 0
    flow_s = clamp(0.6 * rec60 + 0.4 * rec240, -1, 1) * 100
    return [
        {"k": "TREND", "v": round(trend_s)},
        {"k": "MOMENTUM", "v": round(mom_s)},
        {"k": "VOLUME", "v": round(volu_s)},
        {"k": "VOLATILITY", "v": round(vola_s), "note": "favorability"},
        {"k": "STRUCTURE", "v": round(structure_s)},
        {"k": "LIQUIDITY", "v": round(liq_s), "note": "favorability"},
        {"k": "MACRO", "v": round(macro * 100)},
        {"k": "FLOW (proxy)", "v": round(flow_s)},
        {"k": "CONFIDENCE", "v": conf},
    ]


def grade_for(conf, aligned, regime, macro, score):
    agree = (score > 0) == (macro > 0) or abs(macro) < 0.05
    if conf >= 75 and aligned and regime.get("state") == "TRENDING" and agree:
        return "A+"
    if conf >= 65 and (aligned or regime.get("state") == "TRENDING"):
        return "A"
    if conf >= 50:
        return "B"
    if conf >= 35:
        return "C"
    return "D"


def entry_models(sym, direction, px, atr, cc, conf, ema20h):
    if direction == "NEUTRAL":
        return []
    dp = SYMBOLS[sym]["dp"]
    sgn = 1 if direction == "LONG" else -1
    lo_off, hi_off = cc.get("lo_off"), cc.get("hi_off")
    struct_off = lo_off if direction == "LONG" else hi_off
    brk_off = hi_off if direction == "LONG" else lo_off

    def mk(name, entry, sl_dist, prob, note):
        sl_dist = round(max(sl_dist, 0.4 * atr), dp)
        tps = [round(entry + sgn * sl_dist * m, dp) for m in (1, 2, 3)]
        return {"name": name, "entry": round(entry, dp),
                "sl": round(entry - sgn * sl_dist, dp), "sl_dist": sl_dist,
                "tp1": tps[0], "tp2": tps[1], "tp3": tps[2],
                "rr": 2.0, "prob": min(90, max(15, prob)), "note": note}

    structure_sl = (struct_off + 0.3 * atr) if (struct_off and struct_off > 0
                    and 0.6 * atr <= struct_off + 0.3 * atr <= 2.5 * atr) else None
    base_sl = structure_sl or 1.5 * atr
    models = [mk("MARKET", px, base_sl, conf,
                 "immediate fill, full structure stop")]
    pull = ema20h if (ema20h and (ema20h - px) * sgn < 0) \
        else px - sgn * 0.5 * atr
    models.append(mk("PULLBACK", pull, base_sl - abs(px - pull) * 0.5,
                     conf + 5, "wait for retrace to value, better RR"))
    if brk_off and brk_off > 0:
        brk = px + sgn * (brk_off + 0.1 * atr)
        models.append(mk("BREAKOUT", brk, 1.2 * atr, conf - 6,
                        "stop-entry beyond the swing, momentum confirmation"))
    models.append(mk("AGGRESSIVE", px, 1.0 * atr, conf - 10,
                     "tight stop, higher RR, lower hit rate"))
    models.append(mk("CONSERVATIVE", pull, base_sl + 0.5 * atr, conf + 8,
                     "widest stop, survives noise, smallest size"))
    return models


def build_reasons(sym, direction, d, matrix, macro_contribs, regime, session,
                  structure_s, macd_hist, cc, px, atr):
    """Deterministic evidence list + invalidation levels (rule-based)."""
    if direction == "NEUTRAL":
        return {"headline": "WHY NO TRADE", "items": [
            {"t": "Composite score inside the neutral band", "ok": True},
            {"t": "Wait for trend, macro and momentum to agree", "ok": True}],
            "invalidate": []}
    bear = direction == "SHORT"
    items = []
    mi = macro_cache.get("items", {})

    def add(cond, text):
        items.append({"t": text, "ok": bool(cond)})

    if "dxy" in mi:
        c = mi["dxy"]["chg_pct"]
        add((c > 0) == bear if MACRO_IMPACT[sym]["dxy"] < 0 else (c > 0) != bear,
            f"Dollar {'strengthening' if c > 0 else 'weakening'} ({c:+.2f}%)")
    if "us10y" in mi:
        b = mi["us10y"].get("chg_bps", 0)
        add((b > 0) == bear,
            f"Bond yields {'rising' if b > 0 else 'falling'} ({b:+.1f}bp)")
    a1h = next((r for r in matrix if r["tf"] == "1h"), None)
    a15 = next((r for r in matrix if r["tf"] == "15m"), None)
    a1d = next((r for r in matrix if r["tf"] == "1D"), None)
    if a1d:
        add(a1d["trend"]["s"] == (-1 if bear else 1),
            f"Daily trend {'bearish' if bear else 'bullish'}")
    if a15 and a1h:
        both = a15["rating"]["s"] == a1h["rating"]["s"] == (-1 if bear else 1)
        add(both, "15m and 1h aligned")
    if macd_hist is not None:
        add((macd_hist < 0) == bear,
            f"MACD(1h) {'negative' if macd_hist < 0 else 'positive'} ({macd_hist:+.1f})")
    add(regime.get("state") == "TRENDING",
        f"ADX {regime.get('adx')} - {'strong trend' if regime.get('state')=='TRENDING' else 'weak trend'}")
    add((structure_s < 0) == bear if structure_s != 0 else False,
        "Market structure " + ("broken down" if structure_s < 0
                               else "broken up" if structure_s > 0
                               else "intact"))
    add(session.get("liq") != "low",
        f"{session.get('name')} session - {session.get('liq')} liquidity")

    dp = SYMBOLS[sym]["dp"]
    inv = []
    if cc.get("hi_off") and bear:
        inv.append(f"Break above swing high {round(px + cc['hi_off'], dp)}")
    if cc.get("lo_off") and not bear:
        inv.append(f"Break below swing low {round(px - cc['lo_off'], dp)}")
    inv.append(f"{'Reclaim' if bear else 'Loss'} of 1h EMA20")
    inv.append("Macro score flipping sign (dollar/yields reversal)")
    inv.append("Composite score re-entering the neutral band (±0.18)")
    return {"headline": f"WHY {'SHORT' if bear else 'LONG'}?",
            "items": items, "invalidate": inv}


def push_alert(sym, typ, msg):
    alerts[sym].appendleft({"ts": time.strftime("%H:%M:%S"),
                            "type": typ, "msg": msg})


def detect_alerts(sym, sig):
    p = prev_state[sym]
    if p.get("direction") and p["direction"] != sig["direction"]:
        push_alert(sym, "SIGNAL",
                   f"Direction flip {p['direction']} -> {sig['direction']} @ {sig['price']}")
    if sig.get("aligned") and not p.get("aligned"):
        push_alert(sym, "TREND", "Trend alignment complete (15m/1h/4h)")
    if p.get("regime") and p["regime"] != sig["regime"]["state"]:
        push_alert(sym, "REGIME",
                   f"Volatility regime {p['regime']} -> {sig['regime']['state']}")
    mnow = 1 if sig["macro_score"] > 0.1 else (-1 if sig["macro_score"] < -0.1 else 0)
    if p.get("macro_sign") is not None and mnow != p["macro_sign"] and mnow != 0:
        push_alert(sym, "MACRO",
                   f"Macro shifted {'bullish' if mnow > 0 else 'bearish'}")
    if sig.get("grade") in ("A+", "A") and p.get("grade") not in ("A+", "A") \
            and sig["direction"] != "NEUTRAL":
        push_alert(sym, "SETUP",
                   f"High probability setup: {sig['direction']} grade {sig['grade']}")
    vs = next((s for s in sig["scores"] if s["k"] == "VOLUME"), None)
    if vs and abs(vs["v"]) >= 60 and abs(p.get("volume_s", 0)) < 60:
        push_alert(sym, "VOLUME", "Volume spike vs 10-day average")
    prev_state[sym] = {"direction": sig["direction"], "aligned": sig["aligned"],
                       "regime": sig["regime"]["state"], "macro_sign": mnow,
                       "grade": sig.get("grade"),
                       "volume_s": vs["v"] if vs else 0}


def build_signal(sym, d):
    cfg = SYMBOLS[sym]
    if not isinstance(d, dict):
        return {"status": "error", "error": f"scanner returned no data for {cfg['tv']}"}
    px = d.get("close")
    if px is None:
        return {"status": "error", "error": "no price"}

    # delayed futures feeds (e.g. NYMEX:CL1!) only carry daily TA on the
    # scanner - detect and fall back to a pure daily-timeframe model
    intraday = d.get("Recommend.All|60") is not None
    rec15 = d.get("Recommend.All|15") or 0.0
    rec60 = d.get("Recommend.All|60") or 0.0
    rec240 = d.get("Recommend.All|240") or 0.0
    rec1d = d.get("Recommend.All") or 0.0
    rsi15 = d.get("RSI|15")
    e20h, e50h, e200h = d.get("EMA20|60"), d.get("EMA50|60"), d.get("EMA200|60")
    if not intraday:
        e20h = e20h or d.get("EMA20")
        e50h = e50h or d.get("EMA50")
        e200h = e200h or d.get("EMA200")

    tech = (0.35 * rec15 + 0.30 * rec60 + 0.20 * rec240 + 0.15 * rec1d) \
        if intraday else rec1d
    tp = 0
    if e20h and e50h:
        tp += 1 if e20h > e50h else -1
    if e50h and e200h:
        tp += 1 if e50h > e200h else -1
    if e20h:
        tp += 1 if px > e20h else -1
    tech += 0.05 * tp

    warn = []
    if not intraday:
        warn.append("Feed carries daily TA only - running daily-timeframe model")
    if rsi15 is not None:
        if rsi15 > 75 and tech > 0:
            tech *= 0.5
            warn.append("15m RSI overbought - longs are chasing")
        if rsi15 < 25 and tech < 0:
            tech *= 0.5
            warn.append("15m RSI oversold - shorts are chasing")

    macd_hist = None
    if d.get("MACD.macd|60") is not None and d.get("MACD.signal|60") is not None:
        macd_hist = round(d["MACD.macd|60"] - d["MACD.signal|60"], 2)
        tech += 0.06 if macd_hist > 0 else -0.06

    macro, macro_contribs = macro_score_for(cfg["macro_w"])
    score = 0.72 * tech + 0.28 * macro
    if tech * macro < -0.03:
        warn.append("Macro and technicals disagree - reduced conviction")

    adx = d.get("ADX|60") if intraday else d.get("ADX")
    atr_h = d.get("ATR|60") or d.get("ATR|15") or (d.get("ATR", 0) / 4) \
        or px * 0.0015
    atr_d = d.get("ATR") or atr_h * 4.9
    vol_ratio = (atr_h * 4.9) / atr_d if atr_d else 1.0
    vol_regime = ("LOW" if vol_ratio < 0.7 else "NORMAL" if vol_ratio < 1.3
                  else "HIGH" if vol_ratio < 2.0 else "EXTREME")
    if adx is None:
        regime = {"adx": None, "state": "UNKNOWN", "mult": 1.0, "vol": vol_regime}
    elif adx >= 25:
        regime = {"adx": round(adx, 1), "state": "TRENDING", "mult": 1.0,
                  "vol": vol_regime}
    elif adx >= 18:
        regime = {"adx": round(adx, 1), "state": "MILD TREND", "mult": 0.85,
                  "vol": vol_regime}
    else:
        regime = {"adx": round(adx, 1), "state": "RANGING", "mult": 0.6,
                  "vol": vol_regime}
        warn.append(f"1h ADX {adx:.0f} - ranging, chop filter active")
    score *= regime["mult"]

    if not cfg["session_filter"]:
        session = {"name": "CRYPTO 24/7", "liq": "normal"}
    else:
        h = time.gmtime().tm_hour
        if 12 <= h < 16:
            session = {"name": "LDN/NY OVERLAP", "liq": "high"}
        elif 7 <= h < 12:
            session = {"name": "LONDON", "liq": "high"}
        elif 16 <= h < 21:
            session = {"name": "NEW YORK", "liq": "normal"}
        else:
            session = {"name": "ASIA/PACIFIC", "liq": "low"}
            score *= 0.85
            warn.append("Low-liquidity session - signals less reliable")

    signs = [1 if r > 0.1 else (-1 if r < -0.1 else 0)
             for r in (rec15, rec60, rec240)]
    aligned = abs(sum(signs)) == 3 and 0 not in signs
    if score >= 0.18:
        direction = "LONG"
    elif score <= -0.18:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"
    confidence = min(95, round(abs(score) * 130 + (12 if aligned else 0)))

    cc = candle_cache[sym]
    structure_s = 0
    if cc.get("hi_off") is not None and cc["hi_off"] < 0:
        structure_s = 70        # trading above the last swing high
    elif cc.get("lo_off") is not None and cc["lo_off"] < 0:
        structure_s = -70       # trading below the last swing low

    matrix = build_matrix(d)
    scores = build_scores(sym, d, matrix, macro, session, regime,
                          structure_s, confidence)
    grade = grade_for(confidence, aligned, regime, macro, score) \
        if direction != "NEUTRAL" else "-"
    models = entry_models(sym, direction, px, atr_h, cc, confidence, e20h)
    stoch_k = d.get("Stoch.K|15")

    sl_dist = models[0]["sl_dist"] if models else round(1.5 * atr_h, 2)
    sl_basis = "structure/ATR hybrid"
    levels = None
    if models:
        m0 = models[0]
        levels = {"entry": m0["entry"], "pullback": models[1]["entry"],
                  "sl": m0["sl"], "tp1": m0["tp1"], "tp2": m0["tp2"],
                  "tp3": m0["tp3"]}

    reasons = build_reasons(sym, direction, d, matrix, macro_contribs, regime,
                            session, structure_s, macd_hist, cc, px, atr_h)

    hold_est = "2-6h" if regime["state"] == "TRENDING" else "1-3h"
    dp = cfg["dp"]
    exp_dd = round(0.6 * sl_dist, dp)
    per_point = (round(100000 / px, 2) if cfg.get("per_point_dynamic")
                 else cfg["per_point"])

    mi = macro_cache.get("items", {})
    macro_rows = []
    for key, _, name, unit in MACRO_SYMS:
        it = mi.get(key)
        if not it:
            continue
        chg = it.get("chg_bps") if unit == "bps" else it["chg_pct"]
        imp = MACRO_IMPACT[sym].get(key, 0)
        eff = 0
        if imp != 0 and chg:
            eff = imp if chg > 0 else -imp
        verb = ("rising" if (chg or 0) > 0 else
                "falling" if (chg or 0) < 0 else "flat")
        eff_txt = ("supportive" if eff > 0 else
                   "a headwind" if eff < 0 else "neutral")
        why_asset = MACRO_ASSET_WHY.get(sym, {}).get(key, "")
        aword = cfg.get("word", sym)
        note = (f"Currently {verb} → {eff_txt} for {aword}. "
                f"{why_asset[0].upper() + why_asset[1:]}." if why_asset else
                f"Currently {verb} → {eff_txt}.")
        macro_rows.append({"k": key, "name": name, "unit": unit,
                           "last": it["last"], "chg": chg, "impact": eff,
                           "why": MACRO_EXPLAIN.get(key, ""),
                           "note": note,
                           "strength": min(3, int(abs(chg or 0) /
                                           (3 if unit == "bps" else 0.7)) + 1)})

    return {
        "status": "ok", "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": int(time.time()),
        "symbol": cfg["tv"], "tv_chart": cfg["tv_chart"], "sym": sym,
        "sym_name": cfg["name"], "word": cfg.get("word", sym),
        "rt": cfg.get("rt", True), "grp": cfg.get("grp", ""),
        "per_point": per_point, "dp": dp,
        "size_note": cfg["size_note"], "spread_est": cfg["spread_est"],
        "price": round(px, dp), "change_pct": round(d.get("change") or 0, 2),
        "day_high": d.get("high"), "day_low": d.get("low"),
        "direction": direction, "confidence": confidence,
        "score": round(score, 3), "tech_score": round(tech, 3),
        "macro_score": macro, "aligned": aligned, "warnings": warn,
        "regime": regime, "session": session, "grade": grade,
        "matrix": matrix, "scores": scores, "models": models,
        "reasons": reasons, "levels": levels, "sl_dist": sl_dist,
        "sl_basis": sl_basis, "atr_1h": round(atr_h, dp),
        "atr_d": round(atr_d, dp), "macd_hist": macd_hist, "stoch_k": stoch_k,
        "hold_est": hold_est, "exp_dd": exp_dd,
        "tradeable": direction != "NEUTRAL" and regime["state"] != "RANGING",
        "tv_user": tv_auth.get("username"),
        "ema": {"h1_20": e20h, "h1_50": e50h, "h1_200": e200h},
        "pivots": {"r2": d.get("Pivot.M.Classic.R2"),
                   "r1": d.get("Pivot.M.Classic.R1"),
                   "p": d.get("Pivot.M.Classic.Middle"),
                   "s1": d.get("Pivot.M.Classic.S1"),
                   "s2": d.get("Pivot.M.Classic.S2")},
        "macro": {"source": macro_cache.get("source"), "rows": macro_rows,
                  "contribs": macro_contribs, "score": macro,
                  "ts": macro_cache.get("ts")},
        "news": news_cache.get(sym, []),
        "alerts": list(alerts[sym]),
    }


# ---------------------------------------------------------------- tracker

class Tracker:
    def __init__(self, path):
        self.path = path
        self.resolved = []
        self.active = None
        self.last_activity = 0
        self.next_id = 1
        self.load()

    def load(self):
        try:
            with open(self.path, "r") as f:
                d = json.load(f)
            self.resolved = d.get("resolved", [])
            self.active = d.get("active")
            self.next_id = d.get("next_id", len(self.resolved) + 1)
            self.last_activity = d.get("last_activity", 0)
        except Exception:
            pass

    def save(self):
        try:
            with open(self.path, "w") as f:
                json.dump({"resolved": self.resolved[-500:],
                           "active": self.active, "next_id": self.next_id,
                           "last_activity": self.last_activity}, f)
        except Exception:
            pass

    def _close(self, outcome, r, px):
        p = self.active
        p.update({"outcome": outcome, "r": round(r, 2), "exit": round(px, 2),
                  "closed": int(time.time()),
                  "dur_min": round((time.time() - p["opened"]) / 60)})
        self.resolved.append(p)
        self.active = None
        self.last_activity = time.time()
        self.save()
        return p

    def update(self, sym, sig):
        if sig.get("status") != "ok":
            return
        px, now = sig["price"], time.time()
        d = sig["direction"]
        if self.active:
            p = self.active
            sgn = 1 if p["dir"] == "LONG" else -1
            mtm_r = sgn * (px - p["entry"]) / p["sl_dist"]
            p["mtm_r"] = round(mtm_r, 2)
            closed = None
            if p["state"] == "open":
                if sgn * (px - p["sl"]) <= 0:
                    closed = self._close("SL", -1.0, px)
                elif sgn * (px - p["tp1"]) >= 0:
                    p["state"] = "runner"
                    p["tp1_ts"] = int(now)
                    self.save()
            if self.active and p["state"] == "runner":
                if sgn * (px - p["tp2"]) >= 0:
                    closed = self._close("TP2", 2.0, px)
                elif sgn * (px - p["entry"]) <= 0:
                    closed = self._close("TP1-BE", 1.0, px)
            if self.active and d != "NEUTRAL" and d != p["dir"]:
                closed = self._close("FLIP", mtm_r, px)
            if self.active and now - p["opened"] > EXPIRY_S:
                closed = self._close("EXPIRY", mtm_r, px)
            if closed:
                push_alert(sym, "TICKET",
                           f"#{closed['id']} {closed['dir']} closed "
                           f"{closed['outcome']} {closed['r']:+.2f}R")
            return
        if d in ("LONG", "SHORT") and sig.get("levels") \
                and sig.get("tradeable", True) \
                and now - self.last_activity >= COOLDOWN_S:
            lv = sig["levels"]
            self.active = {"id": self.next_id, "opened": int(now),
                           "dir": d, "entry": lv["entry"], "sl": lv["sl"],
                           "tp1": lv["tp1"], "tp2": lv["tp2"],
                           "sl_dist": sig["sl_dist"],
                           "conf": sig["confidence"],
                           "grade": sig.get("grade", "-"),
                           "state": "open", "mtm_r": 0.0}
            self.next_id += 1
            self.last_activity = now
            self.save()
            push_alert(sym, "TICKET",
                       f"#{self.active['id']} {d} opened @ {lv['entry']}")

    def perf(self):
        rs = [p["r"] for p in self.resolved]
        wins = [r for r in rs if r > 0.05]
        losses = [r for r in rs if r < -0.05]
        cum, curve, peak, max_dd = 0.0, [], 0.0, 0.0
        for p in self.resolved:
            cum += p["r"]
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
            curve.append({"id": p["id"], "r": p["r"], "cum": round(cum, 2),
                          "dir": p["dir"], "outcome": p["outcome"]})
        pf = round(sum(wins) / abs(sum(losses)), 2) if losses and wins else None
        sharpe = sortino = None
        if len(rs) >= 3:
            mu = statistics.mean(rs)
            sd = statistics.pstdev(rs)
            sharpe = round(mu / sd, 2) if sd else None
            dn = [r for r in rs if r < 0]
            dsd = statistics.pstdev(dn) if len(dn) >= 2 else None
            sortino = round(mu / dsd, 2) if dsd else None
        streak_w = streak_l = cw = cl = 0
        for r in rs:
            if r > 0.05:
                cw += 1
                cl = 0
            elif r < -0.05:
                cl += 1
                cw = 0
            else:
                cw = cl = 0
            streak_w = max(streak_w, cw)
            streak_l = max(streak_l, cl)
        daily = {}
        for p in self.resolved:
            day = time.strftime("%m-%d", time.localtime(p.get("closed",
                                                             p["opened"])))
            daily[day] = round(daily.get(day, 0) + p["r"], 2)
        today = time.strftime("%m-%d")
        outcomes = {}
        for p in self.resolved:
            outcomes[p["outcome"]] = outcomes.get(p["outcome"], 0) + 1
        by_dir = {}
        for side in ("LONG", "SHORT"):
            sub = [p["r"] for p in self.resolved if p["dir"] == side]
            if sub:
                by_dir[side] = {"n": len(sub),
                                "wins": sum(1 for r in sub if r > 0.05),
                                "avg_r": round(sum(sub) / len(sub), 2)}
        wr = round(100 * len(wins) / len(rs)) if rs else None
        avg_w = round(statistics.mean(wins), 2) if wins else None
        avg_l = round(abs(statistics.mean(losses)), 2) if losses else None
        kelly = None
        if wr is not None and avg_w and avg_l:
            b = avg_w / avg_l
            kelly = round(max(0.0, (wr / 100) - (1 - wr / 100) / b) * 100, 1)
        return {"n": len(rs), "wins": len(wins), "losses": len(losses),
                "win_rate": wr, "total_r": round(sum(rs), 2),
                "avg_r": round(statistics.mean(rs), 2) if rs else None,
                "profit_factor": pf, "sharpe": sharpe, "sortino": sortino,
                "expectancy": round(statistics.mean(rs), 2) if rs else None,
                "max_dd": round(max_dd, 2) if rs else None,
                "avg_hold": round(statistics.mean(
                    [p["dur_min"] for p in self.resolved]), 0) if rs else None,
                "streak_w": streak_w, "streak_l": streak_l,
                "kelly": kelly, "avg_win": avg_w, "avg_loss": avg_l,
                "daily": daily, "today_r": daily.get(today, 0.0),
                "curve": curve, "outcomes": outcomes, "by_dir": by_dir,
                "active": self.active,
                "recent": list(reversed(self.resolved[-20:]))}


trackers = {s: Tracker(SYMBOLS[s]["pred_file"]) for s in SYMBOLS}


# ---------------------------------------------------------------- copilot

def copilot_answer(sym, q):
    """Deterministic desk assistant: answers built ONLY from live state."""
    with state_lock:
        sig = dict(latest.get(sym) or {})
    if sig.get("status") != "ok":
        return "Feed not ready yet - ask again in a few seconds."
    ql = q.lower()
    d = sig["direction"]
    conf = sig["confidence"]

    def evidence():
        rs = sig["reasons"]
        ok = [i["t"] for i in rs["items"] if i["ok"]]
        return "; ".join(ok[:6]) or "no strong evidence"

    if "why" in ql:
        if d == "NEUTRAL":
            return ("No position bias. Composite score {} is inside the "
                    "neutral band. Waiting for trend, macro and momentum "
                    "to agree.").format(sig["score"])
        return (f"{d} with {conf}% confidence. Evidence: {evidence()}. "
                f"Invalidation: {sig['reasons']['invalidate'][0] if sig['reasons']['invalidate'] else 'n/a'}.")
    if "wait" in ql or "should i" in ql:
        if not sig["tradeable"]:
            return ("Yes - wait. " + ("Market is RANGING (ADX "
                    f"{sig['regime']['adx']}), chop filter is active."
                    if sig["regime"]["state"] == "RANGING"
                    else "Signal is neutral."))
        g = sig["grade"]
        return (f"Setup is grade {g}, {conf}% confidence, {sig['regime']['state']}. "
                + ("Acceptable to act with proper size."
                   if g in ("A+", "A", "B") else
                   "Low grade - waiting costs nothing."))
    if "trend" in ql and "strong" in ql or "how strong" in ql:
        r = sig["regime"]
        al = "aligned" if sig["aligned"] else "mixed"
        return (f"ADX(1h) {r['adx']} = {r['state']}. Timeframes {al}. "
                f"Trend score {next((s['v'] for s in sig['scores'] if s['k']=='TREND'), '?')}/100.")
    if "fake" in ql or "breakout" in ql:
        vs = next((s["v"] for s in sig["scores"] if s["k"] == "VOLUME"), 0)
        st = next((s["v"] for s in sig["scores"] if s["k"] == "STRUCTURE"), 0)
        verdict = ("Volume supports the move" if abs(vs) >= 30
                   else "Volume is NOT confirming - breakout suspect")
        return (f"Structure score {st}, volume score {vs}. {verdict}. "
                "Prefer the retest entry over chasing.")
    if "institution" in ql or "positioned" in ql:
        fl = next((s["v"] for s in sig["scores"] if s["k"] == "FLOW (proxy)"), 0)
        return (f"Flow proxy (1h/4h systematic rating) is {fl}/100 - "
                f"{'short' if fl < 0 else 'long'}-side pressure. Stops likely "
                "cluster beyond the recent 15m swing points - expect sweeps "
                "there before reversals.")
    if "safe" in ql or "entry" in ql:
        ms = sig.get("models") or []
        c = next((m for m in ms if m["name"] == "CONSERVATIVE"), None)
        if not c:
            return "No trade - signal is neutral."
        return (f"Safest: CONSERVATIVE {d} - entry {c['entry']}, stop {c['sl']}, "
                f"TP1 {c['tp1']} ({c['prob']}% est). Smallest size, widest stop.")
    # default: summary
    return (f"{sig['sym_name']}: {sig['price']} ({sig['change_pct']:+.2f}%). "
            f"Bias {d} {conf}%, grade {sig['grade']}. "
            f"Regime {sig['regime']['state']}, vol {sig['regime']['vol']}, "
            f"{sig['session']['name']}. Macro {sig['macro_score']:+.2f}. "
            f"Evidence: {evidence()}.")


# ---------------------------------------------------------------- loop

def poller():
    n = 0
    while True:
        if n % MACRO_EVERY == 0:
            try:
                refresh_macro()
            except Exception:
                pass
        for sym in SYMBOLS:
            try:
                if n % MACRO_EVERY == 0:
                    try:
                        refresh_candles(sym)
                    except Exception:
                        pass
                if n % NEWS_EVERY == 0:
                    try:
                        fetch_news(sym)
                    except Exception:
                        pass
                sig = build_signal(sym, fetch_tv(SYMBOLS[sym]["tv"]))
                if sig.get("price"):
                    price_history[sym].append([int(time.time()), sig["price"]])
                    trackers[sym].update(sym, sig)
                    detect_alerts(sym, sig)
                    sig["alerts"] = list(alerts[sym])
                sig["history"] = list(price_history[sym])
                sig["perf"] = trackers[sym].perf()
                with state_lock:
                    latest[sym] = sig
            except Exception as e:
                with state_lock:
                    latest[sym] = {"status": "error", "error": str(e),
                                   "ts": time.strftime("%H:%M:%S"),
                                   "history": list(price_history[sym])}
        n += 1
        time.sleep(POLL_SECONDS)


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _authorized(self):
        if not AUTH_PASS:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(h[6:]).decode().partition(":")
                return u == AUTH_USER and p == AUTH_PASS
            except Exception:
                return False
        return False

    def _deny(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Signal Desk"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._authorized():
            return self._deny()
        if self.path != "/api/tv_login":
            self.send_response(404)
            self.end_headers()
            return
        try:
            ln = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(ln) or b"{}")
        except Exception:
            body = {}
        mode = body.get("mode")
        if mode == "logout":
            tv_auth.update({"sessionid": None, "sessionid_sign": None,
                            "username": None})
            save_auth()
            resp = {"ok": True, "username": None}
        elif mode == "cookie":
            sid = (body.get("sessionid") or "").strip()
            sign = (body.get("sessionid_sign") or "").strip() or None
            user = tv_validate_cookie(sid, sign) if sid else None
            if user:
                tv_auth.update({"sessionid": sid, "sessionid_sign": sign,
                                "username": user})
                save_auth()
                resp = {"ok": True, "username": user}
            else:
                resp = {"ok": False, "error": "cookie rejected - copy fresh "
                        "sessionid AND sessionid_sign while logged in"}
        elif mode == "password":
            r = tv_login_password(body.get("username", ""),
                                  body.get("password", ""))
            if r.get("ok"):
                tv_auth.update({"sessionid": r["sessionid"],
                                "sessionid_sign": r.get("sessionid_sign"),
                                "username": r["username"]})
                save_auth()
                resp = {"ok": True, "username": r["username"]}
            else:
                resp = r
        else:
            resp = {"ok": False, "error": "bad request"}
        self._send(json.dumps(resp).encode(), "application/json")

    def do_GET(self):
        if not self._authorized():
            return self._deny()
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        sym = (qs.get("sym") or ["XAUUSD"])[0]
        if sym not in SYMBOLS:
            sym = "XAUUSD"
        if parsed.path == "/api/signal":
            with state_lock:
                body = json.dumps(latest[sym]).encode()
            self._send(body, "application/json")
        elif parsed.path == "/api/ask":
            q = (qs.get("q") or [""])[0]
            ans = copilot_answer(sym, q)
            self._send(json.dumps({"answer": ans}).encode(),
                       "application/json")
        elif parsed.path == "/" or parsed.path.startswith("/index"):
            try:
                with open(DASH_FILE, "rb") as f:
                    body = f.read()
            except Exception:
                body = b"<h3>dashboard.html missing</h3>"
            self._send(body, "text/html; charset=utf-8")
        else:
            self.send_response(404)
            self.end_headers()


def main():
    try:
        # 0.0.0.0 so the dashboard is reachable over Tailscale / LAN.
        # The Windows Firewall rule limits inbound to the Tailscale range.
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError:
        sys.exit(0)
    load_auth()
    threading.Thread(target=poller, daemon=True).start()
    print(f"Signal Desk v4 -> http://localhost:{PORT} (and Tailscale devices)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
