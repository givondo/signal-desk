"""Automatic trade journal for Northchart.

Every trade you take reaches the desk as a fill (TradingView trade sync, broker
imports). This module turns fills into journal rows without you typing them:

* Desk context at entry. The desk records a compact snapshot of every
  instrument every five minutes (bias, grade, mode, regime, session, levels).
  A fill is matched to the snapshot in force when it opened, so the journal
  says whether you traded with or against the desk and in what conditions.
* Stop and risk. The broker's stop is used when the fill has one. Otherwise,
  when you traded in the desk's direction near its entry, the desk's stop is
  used and labelled as such. Money at risk is worked out from the trade's own
  profit per point (no contract-size guesses), so R multiples are exact for
  that trade.
* Integrity. The sealed, append-only journal (trading_tracker) is unchanged:
  a fill enters it only with a defined stop and risk. Fills without one still
  show in the sheet, marked "Needs stop", and join the journal as soon as you
  type the stop in. Later edits are recorded as corrections, never overwrites.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

HISTORY_EVERY = 300          # seconds between desk snapshots per instrument
HISTORY_DAYS = 8
MATCH_WINDOW = 1800          # a snapshot must be at most 30 minutes before the fill opened
EDITABLE = {"notes": "lessons", "emotion": "emotional_state", "stop": "stop_loss", "setup": "setup"}
EMOTIONS = ("Calm", "Confident", "Patient", "Anxious", "FOMO", "Revenge", "Tired", "Bored")


def _num(v):
    try:
        if v in (None, ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _epoch(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v) / (1000 if v > 1e12 else 1)
    s = str(v).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return _num(v)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def session_for(ts):
    """Trading session by UTC hour, used when no desk snapshot is available."""
    if ts is None:
        return ""
    h = datetime.fromtimestamp(ts, timezone.utc).hour
    if h < 7:
        return "Asia"
    if h < 12:
        return "London"
    if h < 16:
        return "London/New York overlap"
    if h < 21:
        return "New York"
    return "Off-hours"


class Journal:
    def __init__(self, data_dir, symbols_fn, tracker_fn):
        self.symbols = symbols_fn
        self.tracker = tracker_fn          # returns the trading_tracker module or None
        self.dir = os.path.join(data_dir, "journal")
        self.hist_path = os.path.join(self.dir, "desk_history.jsonl")
        self.lock = threading.Lock()
        self.history = {}                  # sym -> list of snapshots (oldest first)
        self.last_snap = {}
        self.last_auto = 0.0
        self._loaded = False

    # ------------------------------------------------------------ desk history
    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        cutoff = time.time() - HISTORY_DAYS * 86400
        try:
            with open(self.hist_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        s = json.loads(line)
                    except ValueError:
                        continue
                    if s.get("t", 0) >= cutoff:
                        self.history.setdefault(s["sym"], []).append(s)
        except OSError:
            pass
        for sym, rows in self.history.items():
            self.last_snap[sym] = rows[-1]["t"] if rows else 0

    def record_desk(self, latest: dict, now=None):
        """Store a compact desk snapshot per instrument, at most every five minutes."""
        now = now or time.time()
        with self.lock:
            self._load()
            lines = []
            for sym, st in (latest or {}).items():
                if (st or {}).get("status") != "ok" or now - self.last_snap.get(sym, 0) < HISTORY_EVERY:
                    continue
                lv = st.get("levels") or {}
                reg = st.get("regime") if isinstance(st.get("regime"), dict) else {}
                ses = st.get("session") if isinstance(st.get("session"), dict) else {}
                snap = {"t": int(now), "sym": sym, "price": st.get("price"), "dir": st.get("direction"),
                        "grade": st.get("grade"), "conf": st.get("confidence"),
                        "mode": (st.get("desk_action") or {}).get("mode"), "regime": reg.get("state"),
                        "session": ses.get("name"), "entry": lv.get("entry"), "sl": lv.get("sl"),
                        "tp1": lv.get("tp1"), "tp2": lv.get("tp2")}
                self.history.setdefault(sym, []).append(snap)
                self.last_snap[sym] = now
                lines.append(json.dumps(snap))
            if lines:
                os.makedirs(self.dir, exist_ok=True)
                with open(self.hist_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                self._trim(now)

    def _trim(self, now):
        cutoff = now - HISTORY_DAYS * 86400
        for sym in list(self.history):
            self.history[sym] = [s for s in self.history[sym] if s["t"] >= cutoff]
        try:
            if os.path.getsize(self.hist_path) > 12_000_000:
                tmp = self.hist_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    for rows in self.history.values():
                        for s in rows:
                            f.write(json.dumps(s) + "\n")
                os.replace(tmp, self.hist_path)
        except OSError:
            pass

    def desk_at(self, sym, ts):
        """The desk snapshot in force when a trade opened, or None."""
        if not sym or ts is None:
            return None
        with self.lock:
            self._load()
            best = None
            for s in self.history.get(sym, []):
                if s["t"] <= ts + 60:
                    best = s
                else:
                    break
        if best and ts - best["t"] <= MATCH_WINDOW:
            return best
        return None

    # ------------------------------------------------------------ helpers
    def symbol_of(self, instrument):
        raw = "".join(ch for ch in str(instrument or "").upper() if ch.isalnum())
        aliases = {"GOLD": "XAUUSD", "SILVER": "XAGUSD", "BTCUSDT": "BTCUSD", "ETHUSDT": "ETHUSD", "US30USD": "US30",
                   "DJ30": "US30", "WS30": "US30", "NAS100USD": "NAS100", "USTEC": "NAS100", "NDX": "NAS100",
                   "WTI": "USOIL", "USOUSD": "USOIL", "XTIUSD": "USOIL", "WTICOUSD": "USOIL"}
        syms = self.symbols()
        for key in sorted(syms, key=len, reverse=True):
            if key in raw:
                return key
        for a, k in aliases.items():
            if a in raw and k in syms:
                return k
        return None

    def _money_per_point(self, fill, sym):
        """Money per 1.0 price move for this trade, from its own P/L when possible."""
        entry, exit_, vol = _num(fill.get("entry_price")), _num(fill.get("exit_price")), _num(fill.get("volume"))
        pl = _num(fill.get("gross_pl"))
        if pl is None:
            pl = _num(fill.get("net_pl"))
        if entry is not None and exit_ is not None and pl not in (None, 0) and abs(exit_ - entry) > 1e-9:
            return abs(pl) / abs(exit_ - entry), "from the trade's own P/L per point"
        pp = (self.symbols().get(sym) or {}).get("per_point")
        if pp and vol:
            return float(pp) * vol, "from the instrument's contract size (estimate)"
        return None, ""

    def enrich(self, fill):
        """Desk context, stop and risk for one fill (nothing is written)."""
        sym = self.symbol_of(fill.get("instrument"))
        t_open = _epoch(fill.get("open_time_utc"))
        snap = self.desk_at(sym, t_open)
        side = "LONG" if str(fill.get("direction")).upper() in ("BUY", "LONG") else "SHORT"
        entry = _num(fill.get("entry_price"))
        out = {"symbol": sym, "snap": snap, "session": (snap or {}).get("session") or session_for(t_open)}
        if snap and snap.get("dir") in ("LONG", "SHORT"):
            rel = "With desk" if snap["dir"] == side else "Against desk"
            out["alignment"] = f"{rel} {snap['dir']} {snap.get('grade') or ''}".strip()
        elif snap:
            out["alignment"] = "Desk neutral"
        else:
            out["alignment"] = "No desk record"
        out["conditions"] = (f"Desk {snap.get('mode') or '-'}, {str(snap.get('regime') or '-').lower()} regime, "
                             f"{snap.get('conf')}% confidence" if snap else "")
        stop, source = _num(fill.get("stop_loss")), "broker stop"
        if stop is None and snap and snap.get("dir") == side and _num(snap.get("sl")) is not None and entry is not None:
            d_entry, d_sl = _num(snap.get("entry")) or entry, _num(snap["sl"])
            if abs(d_entry - d_sl) > 0 and abs(entry - d_entry) <= 1.5 * abs(d_entry - d_sl) \
                    and (d_sl < entry if side == "LONG" else d_sl > entry):
                stop, source = d_sl, "desk stop at entry"
        out["stop"], out["stop_source"] = stop, (source if stop is not None else "")
        if stop is not None and entry is not None:
            mpp, how = self._money_per_point(fill, sym)
            if mpp:
                out["risk"] = round(abs(entry - stop) * mpp, 2)
                out["risk_basis"] = how
        return out

    # ------------------------------------------------------------ automatic pass
    def auto_pass(self, force=False):
        """Journal every pending fill that now has a defined stop and risk."""
        tt = self.tracker()
        if tt is None:
            return {"journaled": 0, "pending": 0, "errors": ["tracker not installed"]}
        if not force and time.time() - self.last_auto < 30:
            return {"journaled": 0, "pending": None, "errors": []}
        self.last_auto = time.time()
        done, errors = 0, []
        pending = tt.pending_fills()
        for f in pending:
            e = self.enrich(f)
            if e.get("stop") is None or not e.get("risk"):
                continue
            try:
                tt.journal_fill(f["fill_id"], {
                    "stop_loss": e["stop"], "initial_risk_amount": e["risk"], "session": e["session"],
                    "setup": e["alignment"], "market_conditions": e["conditions"], "rule_compliance": "FOLLOWED",
                    "lessons": f"Auto-journaled: {e['stop_source']}; risk {e['risk_basis']}. Not yet reviewed."})
                done += 1
            except Exception as ex:  # one bad fill must not stop the rest
                errors.append(f"{f.get('fill_id')}: {ex}")
        return {"journaled": done, "pending": len(pending) - done, "errors": errors[:10]}

    # ------------------------------------------------------------ the sheet
    def sheet(self, include_paper=False, limit=1000):
        tt = self.tracker()
        if tt is None:
            return {"rows": [], "stats": {}, "error": "tracker not installed"}
        self.auto_pass()
        corr = {}
        for c in tt.read("corrections"):
            corr.setdefault(c.get("trade_id"), {})[c.get("field")] = c.get("corrected_value")
        rows = []
        for t in tt.read("trades"):
            paper = t.get("account_label") == tt.PAPER_LABEL or t.get("account_type") == "PAPER" \
                and "paper engine" in (t.get("firm_or_broker") or "").lower()
            if paper and not include_paper:
                continue
            fix = corr.get(t.get("trade_id"), {})
            r = {**t, **fix}
            entry, stop = _num(r.get("entry_price")), _num(r.get("stop_loss"))
            risk = _num(r.get("initial_risk_amount"))
            if "stop_loss" in fix and entry is not None and stop is not None:
                mpp, _ = self._money_per_point(r, self.symbol_of(r.get("instrument")))
                if mpp:
                    risk = round(abs(entry - stop) * mpp, 2)
            net = _num(r.get("net_pl"))
            rmult = round(net / risk, 2) if net is not None and risk else _num(r.get("r_multiple"))
            if not paper and (not r.get("session") or not r.get("setup")):
                e = self.enrich({**r, "open_time_utc": r.get("entry_time_utc")})   # older rows: fill in for display
                r = {**r, "session": r.get("session") or e["session"], "setup": r.get("setup") or e["alignment"],
                     "market_conditions": r.get("market_conditions") or e["conditions"]}
            rows.append(self._row(r, "Complete", risk, rmult, net, "Desk paper" if paper else "Journal"))
        for f in tt.pending_fills():
            e = self.enrich(f)
            net = _num(f.get("net_pl"))
            if net is None and _num(f.get("gross_pl")) is not None:
                net = _num(f.get("gross_pl")) - (_num(f.get("commission")) or 0) - (_num(f.get("swap")) or 0)
            r = {"trade_id": f.get("fill_id"), "account_label": f.get("account_label"),
                 "account_type": f.get("account_type"), "instrument": f.get("instrument"),
                 "direction": f.get("direction"), "entry_time_utc": f.get("open_time_utc"),
                 "exit_time_utc": f.get("close_time_utc"), "entry_price": f.get("entry_price"),
                 "exit_price": f.get("exit_price"), "volume": f.get("volume"), "stop_loss": e.get("stop") or "",
                 "currency": f.get("currency"), "session": e.get("session"), "setup": e.get("alignment"),
                 "market_conditions": e.get("conditions"),
                 "firm_or_broker": f"{f.get('broker')} via {f.get('source')}"}
            rows.append(self._row(r, "Needs stop", e.get("risk"), None, net, "Inbox"))
        rows.sort(key=lambda x: x["opened"] or "", reverse=True)
        return {"rows": rows[:limit], "stats": self._stats(rows), "emotions": list(EMOTIONS),
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def _row(self, r, status, risk, rmult, net, origin):
        sym = self.symbol_of(r.get("instrument"))
        return {"id": r.get("trade_id"), "status": status, "origin": origin, "dp": (self.symbols().get(sym) or {}).get("dp"),
                "instrument_raw": r.get("instrument") or "",
                "opened": r.get("entry_time_utc") or "", "closed": r.get("exit_time_utc") or "",
                "account": r.get("account_label") or r.get("account_type") or "", "type": r.get("account_type") or "",
                "instrument": sym or r.get("instrument") or "", "side": r.get("direction") or "",
                "volume": _num(r.get("volume")), "entry": _num(r.get("entry_price")), "stop": _num(r.get("stop_loss")),
                "exit": _num(r.get("exit_price")), "risk": risk, "net": net, "currency": r.get("currency") or "",
                "r": rmult, "session": r.get("session") or "", "setup": r.get("setup") or "",
                "conditions": r.get("market_conditions") or "", "emotion": r.get("emotional_state") or "",
                "notes": r.get("lessons") or "", "source": r.get("firm_or_broker") or ""}

    @staticmethod
    def _stats(rows):
        done = [r for r in rows if r["status"] == "Complete" and r["origin"] != "Desk paper"]
        rs = [r["r"] for r in done if isinstance(r["r"], (int, float))]
        wins = [x for x in rs if x > 0]
        by_ccy = {}
        for r in done:
            if isinstance(r["net"], (int, float)):
                by_ccy[r["currency"] or "USD"] = round(by_ccy.get(r["currency"] or "USD", 0) + r["net"], 2)
        streak = worst = 0
        for r in sorted(done, key=lambda x: x["opened"]):
            if isinstance(r["r"], (int, float)) and r["r"] < 0:
                streak += 1
                worst = max(worst, streak)
            else:
                streak = 0
        return {"trades": len(done), "needs_stop": sum(1 for r in rows if r["status"] == "Needs stop"),
                "win_rate": round(100 * len(wins) / len(rs), 1) if rs else None,
                "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
                "total_r": round(sum(rs), 2) if rs else None,
                "best_r": max(rs) if rs else None, "worst_r": min(rs) if rs else None,
                "net_by_currency": by_ccy, "max_losing_streak": worst,
                "with_desk": sum(1 for r in done if r["setup"].startswith("With desk")),
                "against_desk": sum(1 for r in done if r["setup"].startswith("Against desk"))}

    # ------------------------------------------------------------ edits from the sheet
    def update(self, trade_id, field, value):
        tt = self.tracker()
        if tt is None:
            raise ValueError("tracker not installed")
        if field not in EDITABLE:
            raise ValueError("you can edit notes, emotion, setup or stop")
        value = str(value if value is not None else "").strip()[:1000]
        if field == "emotion" and value and value not in EMOTIONS:
            raise ValueError("emotion must be one of " + ", ".join(EMOTIONS))
        trades = {t.get("trade_id"): t for t in tt.read("trades")}
        if trade_id in trades:
            original = trades[trade_id].get(EDITABLE[field], "")
            if field == "stop" and _num(value) is None:
                raise ValueError("stop must be a number")
            tt.append("corrections", {"trade_id": trade_id, "field": EDITABLE[field], "original_value": original,
                                      "corrected_value": value, "reason": "edited in the journal sheet"})
            return {"ok": True, "journaled": False}
        pending = {f.get("fill_id"): f for f in tt.pending_fills()}
        if trade_id not in pending:
            raise ValueError("unknown trade")
        if field != "stop":
            raise ValueError("add the stop first; notes can be added once the trade is in the journal")
        f, stop = pending[trade_id], _num(value)
        if stop is None:
            raise ValueError("stop must be a number")
        e = self.enrich({**f, "stop_loss": stop})
        if not e.get("risk"):
            raise ValueError("could not work out the money at risk for this trade")
        tt.journal_fill(trade_id, {"stop_loss": stop, "initial_risk_amount": e["risk"], "session": e["session"],
                                   "setup": e["alignment"], "market_conditions": e["conditions"],
                                   "rule_compliance": "FOLLOWED",
                                   "lessons": f"Stop entered in the sheet; risk {e['risk_basis']}."})
        return {"ok": True, "journaled": True}
