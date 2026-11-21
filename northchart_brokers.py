"""Broker connections for Northchart: read-only, per user.

Routes into the journal, best first:

* Northchart Bridge (MetaTrader 4 and 5, free). A small add-on (Expert Advisor) runs in
  the trader's own terminal, reads closed trades from its history and posts them to
  /api/ingest/bridge with the user's sync token. No password leaves their computer and
  the add-on contains no trading calls.
* MetaApi (MetaTrader 4 and 5, paid by the desk). The trader enters login, server and
  the investor (read-only) password once. The desk creates a MetaApi account with it,
  checks MetaApi's investorMode flag and refuses a master password, then keeps only
  the MetaApi account id. The password itself is never stored here.
* Statement import (always available). MT5 and MT4 HTML reports, and any CSV
  (cTrader, prop firms, spreadsheets) with columns detected by name or mapped by hand.
* TradingView trade sync, the existing script, shown as one more connection.

Every route produces the same fills and goes through trading_tracker.import_fills, so
the journal's autofill (desk context, stop, flags, draft notes) works for all of them.

Secrets at rest (the MetaApi account id) are sealed with an HMAC-SHA256 stream cipher
and an HMAC tag (encrypt-then-MAC), keyed from NORTHCHART_SECRET_KEY or, when that is
not set, a random key in DATA_DIR/accounts/secret.key. Standard library only.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import html.parser
import io
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# ------------------------------------------------------------------ catalogue
# Platforms are the ones each broker usually offers; "Any MetaTrader broker" covers the rest.
CMA_NOTE = "Licensed in Kenya by the CMA (check licensees.cma.or.ke before you deposit)"
BROKERS = [
    {"id": "exness", "name": "Exness", "platforms": ["mt5", "mt4"], "cma": True},
    {"id": "hfm", "name": "HFM", "platforms": ["mt5", "mt4"], "cma": True},
    {"id": "fxpesa", "name": "FXPesa (EGM Securities)", "platforms": ["mt5", "mt4"], "cma": True},
    {"id": "pepperstone", "name": "Pepperstone", "platforms": ["mt5", "mt4", "ctrader", "tradingview"], "cma": True},
    {"id": "xm", "name": "XM", "platforms": ["mt5", "mt4"], "cma": True},
    {"id": "scope", "name": "Scope Markets", "platforms": ["mt5", "mt4"], "cma": True},
    {"id": "windsor", "name": "Windsor Markets", "platforms": ["mt4", "mt5"], "cma": True},
    {"id": "fxtm", "name": "FXTM (Exinity)", "platforms": ["mt5", "mt4"], "cma": True},
    {"id": "fpmarkets", "name": "FP Markets", "platforms": ["mt5", "mt4", "ctrader"], "cma": True},
    {"id": "avatrade", "name": "AvaTrade", "platforms": ["mt5", "mt4"], "cma": True},
    {"id": "capitalcom", "name": "Capital.com", "platforms": ["mt4", "tradingview"], "cma": True},
    {"id": "anzo", "name": "Anzo Capital", "platforms": ["mt5", "mt4"], "cma": True},
    {"id": "icmarkets", "name": "IC Markets", "platforms": ["mt5", "mt4", "ctrader", "tradingview"], "cma": False},
    {"id": "deriv", "name": "Deriv", "platforms": ["mt5", "ctrader"], "cma": False},
    {"id": "ftmo", "name": "FTMO (prop firm)", "platforms": ["mt5", "mt4", "ctrader", "dxtrade"], "cma": False},
    {"id": "fundednext", "name": "FundedNext (prop firm)", "platforms": ["mt5", "mt4", "ctrader"], "cma": False},
    {"id": "other_mt", "name": "Any other MetaTrader broker", "platforms": ["mt5", "mt4"], "cma": False},
]
PLANNED = [
    {"id": "ctrader", "name": "cTrader Open API", "why": "Official read access with your cTrader ID. Next on the list."},
    {"id": "oanda", "name": "OANDA", "why": "OANDA tokens can also place trades, so it waits for a read-only option. "
                                         "Use statement import meanwhile."},
    {"id": "ibkr", "name": "Interactive Brokers", "why": "Flex reports are read-only by design. Planned."},
    {"id": "deriv_api", "name": "Deriv API", "why": "Waiting to confirm a read-only scope in Deriv's new API. "
                                                    "Deriv MT5 already works through the Bridge."},
    {"id": "binance", "name": "Binance", "why": "Read-only API keys, checked for trading permission. Planned."},
]
PLATFORM_NAMES = {"mt5": "MetaTrader 5", "mt4": "MetaTrader 4", "ctrader": "cTrader", "tradingview": "TradingView",
                  "dxtrade": "DXtrade"}


def catalogue(metaapi_enabled: bool):
    return {"brokers": [{**b, "platform_names": [PLATFORM_NAMES[p] for p in b["platforms"]],
                         "note": CMA_NOTE if b["cma"] else ""} for b in BROKERS],
            "routes": {"bridge": True, "metaapi": metaapi_enabled, "statement": True, "tradingview": True},
            "planned": PLANNED}


# ------------------------------------------------------------------ sealing secrets
class Vault:
    def __init__(self, data_dir):
        key = os.environ.get("NORTHCHART_SECRET_KEY")
        if key and len(key) >= 24:
            raw = key.encode()
            self.from_env = True
        else:
            path = os.path.join(data_dir, "accounts", "secret.key")
            try:
                with open(path, "rb") as f:
                    raw = f.read()
            except OSError:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                raw = secrets.token_bytes(32)
                with open(path, "wb") as f:
                    f.write(raw)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            self.from_env = False
        self.k_enc = hmac.new(raw, b"northchart-enc-v1", hashlib.sha256).digest()
        self.k_mac = hmac.new(raw, b"northchart-mac-v1", hashlib.sha256).digest()

    def _stream(self, nonce, n):
        out, i = b"", 0
        while len(out) < n:
            out += hmac.new(self.k_enc, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
            i += 1
        return out[:n]

    def seal(self, obj) -> str:
        data = json.dumps(obj).encode()
        nonce = secrets.token_bytes(16)
        ct = bytes(a ^ b for a, b in zip(data, self._stream(nonce, len(data))))
        tag = hmac.new(self.k_mac, nonce + ct, hashlib.sha256).digest()
        return "v1:" + base64.b64encode(nonce + ct + tag).decode()

    def open(self, sealed: str):
        if not sealed or not sealed.startswith("v1:"):
            raise ValueError("unknown secret format")
        blob = base64.b64decode(sealed[3:])
        nonce, ct, tag = blob[:16], blob[16:-32], blob[-32:]
        if not hmac.compare_digest(tag, hmac.new(self.k_mac, nonce + ct, hashlib.sha256).digest()):
            raise ValueError("secret was changed or the key is wrong")
        return json.loads(bytes(a ^ b for a, b in zip(ct, self._stream(nonce, len(ct)))))


# ------------------------------------------------------------------ helpers
def _num(v):
    try:
        if v in (None, ""):
            return None
        return float(str(v).replace(" ", "").replace("\u00a0", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s, offset_hours=0.0):
    """Broker report time (server clock) to UTC ISO. Accepts 2026.11.20 14:03:11, ISO, dd/mm/yyyy."""
    if s in (None, ""):
        return ""
    if isinstance(s, (int, float)):
        return _iso(datetime.fromtimestamp(float(s), timezone.utc))
    s = str(s).strip()
    if s.endswith("Z") or re.search(r"[+-]\d\d:\d\d$", s):
        try:
            return _iso(datetime.fromisoformat(s.replace("Z", "+00:00")))
        except ValueError:
            pass
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d.%m.%Y %H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f", "%d/%m/%Y %H:%M:%S.%f"):
        try:
            d = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return _iso(d - timedelta(hours=offset_hours))
        except ValueError:
            continue
    return ""


def _side(v):
    v = str(v or "").strip().lower()
    if v.startswith(("buy", "long")) or v == "b":
        return "BUY"
    if v.startswith(("sell", "short")) or v == "s":
        return "SELL"
    return ""


class _Tables(html.parser.HTMLParser):
    """All table rows of an HTML document as lists of cell text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell, self.in_cell = [], None, [], False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.in_cell, self.cell = True, []
            span = dict(attrs).get("colspan")
            self._span = int(span) if span and span.isdigit() else 1

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.row is not None and self.in_cell:
            self.row.append(" ".join("".join(self.cell).split()))
            self.row.extend([""] * (self._span - 1))
            self.in_cell = False
        elif tag == "tr" and self.row is not None:
            if any(c for c in self.row):
                self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)


def decode_bytes(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if len(raw) > 4 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
        return raw.decode("utf-16-le", "replace")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


# ------------------------------------------------------------------ statement parsers
def parse_mt5_html(text, offset_hours=0.0):
    """MT5 History report: the Positions block (Time, Position, Symbol, Type, Volume, Price, S / L, T / P,
    Time, Price, Commission, Swap, Profit)."""
    p = _Tables()
    p.feed(text)
    fills, in_pos = [], False
    for row in p.rows:
        first = (row[0] or "").strip().lower()
        if not any(c.strip() for c in row[1:]) and first in ("positions", "orders", "deals", "open positions",
                                                               "working orders", "results"):
            in_pos = first == "positions"
            continue
        if not in_pos:
            continue
        cells = [c for c in row]
        if first == "time":
            continue
        typ = _side(cells[3] if len(cells) > 3 else "")
        if len(cells) < 13 or not typ:
            continue
        sl, tp = _num(cells[6]), _num(cells[7])
        fills.append({"broker_trade_id": cells[1], "instrument": cells[2], "direction": typ,
                      "volume": _num(cells[4].split("/")[0]), "entry_price": _num(cells[5]),
                      "stop_loss": sl if sl else "", "take_profit": tp if tp else "",
                      "open_time_utc": parse_time(cells[0], offset_hours), "close_time_utc": parse_time(cells[8], offset_hours),
                      "exit_price": _num(cells[9]), "commission": _num(cells[10]), "swap": _num(cells[11]),
                      "gross_pl": _num(cells[12]),
                      "net_pl": round((_num(cells[12]) or 0) + (_num(cells[10]) or 0) + (_num(cells[11]) or 0), 2)})
    return fills


def parse_mt4_html(text, offset_hours=0.0):
    """MT4 statement: Closed Transactions (Ticket, Open Time, Type, Size, Item, Price, S / L, T / P,
    Close Time, Price, Commission, Taxes, Swap, Profit)."""
    p = _Tables()
    p.feed(text)
    fills, on = [], False
    for row in p.rows:
        first = (row[0] or "").strip().lower()
        if first.startswith("closed transactions"):
            on = True
            continue
        if first.startswith(("open trades", "working orders", "summary", "closed p/l")):
            on = False
        if not on or first == "ticket" or len(row) < 13:
            continue
        typ = _side(row[2])
        if not typ or row[2].strip().lower() in ("balance", "credit"):
            continue
        n = len(row)
        profit = _num(row[n - 1])
        swap = _num(row[n - 2])
        taxes = _num(row[n - 3]) if n >= 14 else 0
        comm = _num(row[n - 4]) if n >= 14 else _num(row[n - 3])
        sl, tp = _num(row[6]), _num(row[7])
        fills.append({"broker_trade_id": row[0], "instrument": row[4], "direction": typ, "volume": _num(row[3]),
                      "entry_price": _num(row[5]), "stop_loss": sl if sl else "", "take_profit": tp if tp else "",
                      "open_time_utc": parse_time(row[1], offset_hours), "close_time_utc": parse_time(row[8], offset_hours),
                      "exit_price": _num(row[9]), "commission": comm, "swap": swap, "gross_pl": profit,
                      "net_pl": round((profit or 0) + (comm or 0) + (swap or 0) + (taxes or 0), 2)})
    return fills


CSV_FIELDS = {
    "broker_trade_id": ("position id", "position", "ticket", "id", "trade id", "order id", "deal", "order"),
    "instrument": ("symbol", "instrument", "item", "market", "pair", "asset"),
    "direction": ("direction", "side", "type", "action", "buy/sell", "trade type"),
    "volume": ("volume", "lots", "size", "quantity", "qty", "closing quantity", "amount"),
    "open_time_utc": ("open time", "opening time", "entry time", "open date", "time opened", "opened", "open"),
    "close_time_utc": ("close time", "closing time", "exit time", "close date", "time closed", "closed"),
    "entry_price": ("open price", "entry price", "opening price", "entry", "price open"),
    "exit_price": ("close price", "exit price", "closing price", "exit", "price close"),
    "stop_loss": ("stop loss", "sl", "s / l", "s/l", "stop"),
    "take_profit": ("take profit", "tp", "t / p", "t/p", "target"),
    "commission": ("commission", "commissions", "fee", "fees"),
    "swap": ("swap", "swaps", "rollover", "financing"),
    "net_pl": ("net profit", "net p/l", "net pnl", "net usd", "net", "net eur", "net gbp"),
    "gross_pl": ("profit", "gross profit", "p/l", "pnl", "pl", "gross usd", "realized p&l", "realised p&l"),
}
REQUIRED_CSV = ("instrument", "direction", "entry_price", "exit_price")


def detect_mapping(headers):
    low = [h.strip().lower() for h in headers]
    mapping = {}
    for field, names in CSV_FIELDS.items():
        for n in names:                    # exact names first, in priority order
            if n in low and low.index(n) not in mapping.values():
                mapping[field] = low.index(n)
                break
    for field, names in CSV_FIELDS.items():  # then headers that contain a name ("Opening Direction")
        if field in mapping:
            continue
        for n in (x for x in names if len(x) >= 4):
            hit = next((i for i, h in enumerate(low) if n in h and i not in mapping.values()), None)
            if hit is not None:
                mapping[field] = hit
                break
    return mapping


def parse_csv(text, offset_hours=0.0, mapping=None):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return [], {"headers": [], "mapping": {}, "missing": list(REQUIRED_CSV)}
    headers = rows[0]
    m = detect_mapping(headers)
    for k, v in (mapping or {}).items():   # the trader's own choices win
        if k in CSV_FIELDS and v not in (None, ""):
            m[k] = headers.index(v) if isinstance(v, str) and v in headers else int(v)
    missing = [f for f in REQUIRED_CSV if f not in m]
    info = {"headers": headers, "mapping": {k: headers[v] for k, v in m.items() if v < len(headers)},
            "missing": missing}
    if missing:
        return [], info
    g = lambda r, f: (r[m[f]] if f in m and m[f] < len(r) else "")
    fills = []
    for i, r in enumerate(rows[1:], start=2):
        side = _side(g(r, "direction"))
        if not side:
            continue
        net, gross = _num(g(r, "net_pl")), _num(g(r, "gross_pl"))
        comm, swap = _num(g(r, "commission")), _num(g(r, "swap"))
        if net is None and gross is not None:
            net = round(gross + (comm or 0) + (swap or 0), 2)
        sl, tp = _num(g(r, "stop_loss")), _num(g(r, "take_profit"))
        tid = g(r, "broker_trade_id") or hashlib.sha1("|".join(r).encode()).hexdigest()[:12]
        fills.append({"broker_trade_id": tid, "instrument": g(r, "instrument"), "direction": side,
                      "volume": _num(g(r, "volume")), "entry_price": _num(g(r, "entry_price")),
                      "exit_price": _num(g(r, "exit_price")), "stop_loss": sl if sl else "",
                      "take_profit": tp if tp else "",
                      "open_time_utc": parse_time(g(r, "open_time_utc"), offset_hours),
                      "close_time_utc": parse_time(g(r, "close_time_utc"), offset_hours),
                      "commission": comm, "swap": swap, "gross_pl": gross, "net_pl": net})
    return fills, info


def parse_statement(filename, raw: bytes, offset_hours=0.0, mapping=None):
    text = decode_bytes(raw)
    name = (filename or "").lower()
    low = text[:200000].lower()
    if name.endswith((".htm", ".html")) or "<html" in low[:2000] or "<table" in low:
        if "closed transactions" in low:
            return "MetaTrader 4 statement", parse_mt4_html(text, offset_hours), {}
        if "positions" in low:
            return "MetaTrader 5 report", parse_mt5_html(text, offset_hours), {}
        return "HTML file", [], {"error": "No Positions or Closed Transactions table found. In MetaTrader, export "
                                           "History > Report > HTML."}
    if name.endswith((".xlsx", ".xls")):
        return "Excel file", [], {"error": "Save the report as HTML (MetaTrader) or CSV and upload that."}
    fills, info = parse_csv(text, offset_hours, mapping)
    return "CSV file", fills, info


# ------------------------------------------------------------------ the Bridge
def bridge_fills(body):
    """Closed positions posted by the Northchart Bridge add-on, already in UTC."""
    out = []
    for d in body.get("positions") or []:
        if not isinstance(d, dict):
            continue
        side = _side(d.get("type"))
        if not side or not d.get("position"):
            continue
        sl0, sl1 = _num(d.get("sl_initial")), _num(d.get("sl_final"))
        stop = sl0 if sl0 else (sl1 if sl1 else "")
        extras = {"stop_initial": sl0 or None, "stop_final": sl1 or None, "magic": d.get("magic"),
                  "reason": d.get("reason")}
        out.append({"broker_trade_id": str(d["position"]), "instrument": str(d.get("symbol") or ""),
                    "direction": side, "volume": _num(d.get("volume")), "entry_price": _num(d.get("open_price")),
                    "exit_price": _num(d.get("close_price")), "stop_loss": stop,
                    "take_profit": _num(d.get("tp")) or "",
                    "open_time_utc": parse_time(d.get("open_time")), "close_time_utc": parse_time(d.get("close_time")),
                    "commission": _num(d.get("commission")), "swap": _num(d.get("swap")),
                    "gross_pl": _num(d.get("profit")),
                    "net_pl": round((_num(d.get("profit")) or 0) + (_num(d.get("commission")) or 0)
                                    + (_num(d.get("swap")) or 0), 2),
                    "raw": json.dumps({k: v for k, v in extras.items() if v is not None})})
    return out


# ------------------------------------------------------------------ MetaApi
class MetaApi:
    """Thin REST client. Endpoints overridable for tests."""

    def __init__(self, token):
        self.token = token
        self.prov = os.environ.get("NORTHCHART_METAAPI_PROVISIONING_URL",
                                   "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai")
        self.client_tpl = os.environ.get("NORTHCHART_METAAPI_CLIENT_URL",
                                         "https://mt-client-api-v1.{region}.agiliumtrade.ai")

    def _req(self, method, url, body=None, timeout=40):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "auth-token": self.token, "Accept": "application/json", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}")
            except ValueError:
                msg = {}
            return e.code, msg

    def create(self, login, password, server, platform, name):
        st, out = self._req("POST", self.prov + "/users/current/accounts", {
            "login": str(login), "password": password, "server": server, "platform": platform,
            "name": name[:60], "magic": 0, "type": "cloud-g2", "application": "MetaApi"})
        if st not in (200, 201, 202):
            raise ValueError(out.get("message") or f"MetaApi refused the account ({st})")
        return out.get("id"), out.get("region") or os.environ.get("NORTHCHART_METAAPI_REGION", "new-york")

    def remove(self, account_id):
        self._req("DELETE", f"{self.prov}/users/current/accounts/{account_id}")

    def info(self, account_id, region):
        return self._req("GET", f"{self.client_tpl.format(region=region)}/users/current/accounts/{account_id}"
                                "/account-information")

    def deals(self, account_id, region, start, end):
        base = self.client_tpl.format(region=region)
        st, out = self._req("GET", f"{base}/users/current/accounts/{account_id}/history-deals/time/"
                                   f"{start.strftime('%Y-%m-%dT%H:%M:%S.000Z')}/{end.strftime('%Y-%m-%dT%H:%M:%S.000Z')}")
        if st != 200:
            raise ValueError((out or {}).get("message") or f"MetaApi answered {st}")
        return out if isinstance(out, list) else out.get("deals", [])


def pair_deals(deals):
    """MetaApi deals (DEAL_ENTRY_IN / DEAL_ENTRY_OUT) to closed positions."""
    by_pos = {}
    for d in deals:
        if d.get("type") not in ("DEAL_TYPE_BUY", "DEAL_TYPE_SELL") or not d.get("positionId"):
            continue
        by_pos.setdefault(str(d["positionId"]), []).append(d)
    fills = []
    for pid, ds in by_pos.items():
        ins = [d for d in ds if d.get("entryType") == "DEAL_ENTRY_IN"]
        outs = [d for d in ds if d.get("entryType") in ("DEAL_ENTRY_OUT", "DEAL_ENTRY_OUT_BY", "DEAL_ENTRY_INOUT")]
        if not ins or not outs:
            continue
        vol = sum(d.get("volume") or 0 for d in ins)
        avg = lambda xs: sum((x.get("price") or 0) * (x.get("volume") or 0) for x in xs) / max(
            sum(x.get("volume") or 0 for x in xs), 1e-12)
        profit = sum(d.get("profit") or 0 for d in outs)
        comm = sum(d.get("commission") or 0 for d in ds)
        swap = sum(d.get("swap") or 0 for d in ds)
        first = min(ins, key=lambda d: d.get("time") or "")
        sl0, sl1 = _num(first.get("stopLoss")), _num(max(outs, key=lambda d: d.get("time") or "").get("stopLoss"))
        fills.append({"broker_trade_id": pid, "instrument": first.get("symbol") or "",
                      "direction": "BUY" if first["type"] == "DEAL_TYPE_BUY" else "SELL", "volume": round(vol, 4),
                      "entry_price": round(avg(ins), 6), "exit_price": round(avg(outs), 6),
                      "stop_loss": sl0 or "", "open_time_utc": parse_time(first.get("time")),
                      "close_time_utc": parse_time(max(d.get("time") or "" for d in outs)),
                      "commission": round(comm, 2), "swap": round(swap, 2), "gross_pl": round(profit, 2),
                      "net_pl": round(profit + comm + swap, 2),
                      "raw": json.dumps({k: v for k, v in {"stop_initial": sl0, "stop_final": sl1}.items() if v})})
    return fills


# ------------------------------------------------------------------ per-user connections
class Connections:
    def __init__(self, user_dir, vault, tracker_fn):
        self.path = os.path.join(user_dir, "brokers", "connections.json")
        self.vault, self.tracker = vault, tracker_fn
        self.lock = threading.RLock()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"connections": []}

    def _save(self, d):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, self.path)

    def list(self):
        with self.lock:
            return [{k: v for k, v in c.items() if k != "secret"} for c in self._load()["connections"]]

    def get(self, cid):
        with self.lock:
            return next((c for c in self._load()["connections"] if c["id"] == cid), None)

    def upsert(self, match, **fields):
        with self.lock:
            d = self._load()
            c = next((c for c in d["connections"] if all(c.get(k) == v for k, v in match.items())), None)
            if c is None:
                c = {"id": "c_" + secrets.token_hex(5), "created": int(time.time()), "trades": 0, **match}
                d["connections"].append(c)
            c.update({k: v for k, v in fields.items()})
            self._save(d)
            return {k: v for k, v in c.items() if k != "secret"}

    def remove(self, cid):
        with self.lock:
            d = self._load()
            gone = [c for c in d["connections"] if c["id"] == cid]
            d["connections"] = [c for c in d["connections"] if c["id"] != cid]
            self._save(d)
            return gone[0] if gone else None

    def sync_metaapi(self, cid, api, now=None):
        """Pull closed positions from MetaApi into the journal."""
        c = self.get(cid)
        if not c or c.get("kind") != "metaapi":
            raise ValueError("unknown MetaApi connection")
        sec = self.vault.open(c["secret"])
        now = now or datetime.now(timezone.utc)
        since = datetime.fromtimestamp(c["last_sync"], timezone.utc) - timedelta(days=3) if c.get("last_sync") \
            else now - timedelta(days=90)
        try:
            deals = api.deals(sec["account_id"], sec["region"], since, now + timedelta(minutes=5))
        except Exception as e:
            self.upsert({"id": cid}, last_error=str(e)[:200], state="error")
            raise
        fills = pair_deals(deals)
        return self.ingest({"id": cid}, "metaapi", c.get("broker") or "MetaTrader", c.get("account_label"),
                           c.get("account_type") or "PERSONAL", fills)

    def ingest(self, conn_match, source, broker_name, account_label, account_type, fills, **fields):
        tt = self.tracker()
        res = tt.import_fills(source, broker_name, account_label, account_type, fills)
        with self.lock:
            prev = next((c for c in self._load()["connections"]
                         if all(c.get(k) == v for k, v in conn_match.items())), {})
        self.upsert(conn_match, last_sync=int(time.time()), last_error=None, state="connected",
                    trades=(prev.get("trades") or 0) + res.get("added", 0), **fields)
        return res
