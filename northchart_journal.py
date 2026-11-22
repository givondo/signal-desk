"""Automatic trade journal for Northcharts.

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
* Provenance. Stop, emotion and notes each carry their source: Broker, Desk,
  Suggested (a draft or flag the trader has not confirmed) or You. Nothing
  automatic overwrites a value the trader entered. Emotion is never set
  automatically; behaviour flags (Revenge, FOMO, Overtrading, Against desk,
  Moved stop, Early exit, No stop) suggest one for a one-click confirm.
* Review. Every trade waits in a review queue until the trader has confirmed
  the stop (or marked "No stop used"), the emotion and the notes.
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
REVENGE_WINDOW = 900         # a new trade within 15 minutes of a loss on the same instrument
FLAG_EMOTION = (("Revenge", "Revenge"), ("FOMO", "FOMO"), ("Moved stop", "Anxious"),
                ("Early exit", "Anxious"), ("Overtrading", "Bored"))
MACHINE_NOTES = ("auto-journaled", "stop entered in the sheet")


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
        self.meta_path = os.path.join(self.dir, "meta.json")
        self._meta = None

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
                if (st or {}).get("status") != "ok" or abs(now - self.last_snap.get(sym, 0)) < HISTORY_EVERY:
                    continue
                lv = st.get("levels") or {}
                reg = st.get("regime") if isinstance(st.get("regime"), dict) else {}
                ses = st.get("session") if isinstance(st.get("session"), dict) else {}
                snap = {"t": int(now), "sym": sym, "price": st.get("price"), "dir": st.get("direction"),
                        "grade": st.get("grade"), "conf": st.get("confidence"),
                        "mode": (st.get("desk_action") or {}).get("mode"), "regime": reg.get("state"),
                        "session": ses.get("name"), "entry": lv.get("entry"), "sl": lv.get("sl"),
                        "tp1": lv.get("tp1"), "tp2": lv.get("tp2")}
                hist = self.history.setdefault(sym, [])
                hist.append(snap)
                if len(hist) > 1 and hist[-2]["t"] > snap["t"]:
                    hist.sort(key=lambda x: x["t"])
                self.last_snap[sym] = max(now, self.last_snap.get(sym, 0))
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

    # ------------------------------------------------------------ provenance store
    def _meta_all(self):
        if self._meta is None:
            try:
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    self._meta = json.load(f)
            except (OSError, ValueError):
                self._meta = {}
        return self._meta

    def meta(self, trade_id):
        with self.lock:
            return dict(self._meta_all().get(trade_id) or {})

    def _set_meta(self, trade_id, **fields):
        with self.lock:
            m = self._meta_all()
            cur = m.setdefault(trade_id, {})
            for k, v in fields.items():
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = v
            cur["updated"] = int(time.time())
            os.makedirs(self.dir, exist_ok=True)
            tmp = self.meta_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(m, f, indent=1, sort_keys=True)
            os.replace(tmp, self.meta_path)
            return dict(cur)

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
            m = self.meta(f["fill_id"])
            if m.get("stop_mode") == "none":
                continue
            e = self.enrich(f)
            if e.get("stop") is None or not e.get("risk"):
                continue
            try:
                self._journal(tt, f, e, "Desk" if e["stop_source"] == "desk stop at entry" else "Broker", m)
                done += 1
            except Exception as ex:  # one bad fill must not stop the rest
                errors.append(f"{f.get('fill_id')}: {ex}")
        return {"journaled": done, "pending": len(pending) - done, "errors": errors[:10]}

    def _journal(self, tt, f, e, stop_by, m):
        extra = {"stop_loss": e["stop"], "initial_risk_amount": e["risk"], "session": e["session"],
                 "setup": e["alignment"], "market_conditions": e["conditions"], "rule_compliance": "FOLLOWED"}
        if m.get("emotion"):
            extra["emotional_state"] = m["emotion"]
        if m.get("notes"):
            extra["lessons"] = m["notes"]
        tt.journal_fill(f["fill_id"], extra)
        self._set_meta(f["fill_id"], stop_by=stop_by, risk_basis=e.get("risk_basis"))

    # ------------------------------------------------------------ price path and flags
    def path(self, sym, side, entry, stop, t_open, t_close, exit_, tp1=None):
        """Best and worst move in R while the trade was open, from the desk's 5-minute prices."""
        risk = abs(entry - stop) if entry is not None and stop is not None else 0
        if not sym or t_open is None or not risk:
            return {}
        t_close = t_close or t_open
        with self.lock:
            self._load()
            prices = [_num(s.get("price")) for s in self.history.get(sym, []) if t_open <= s["t"] <= t_close]
        prices = [p for p in prices if p is not None]
        if len(prices) < 2:          # too few desk prices inside the trade to say anything honest
            return {}
        prices += [p for p in (exit_,) if p is not None]
        sign = 1 if side == "LONG" else -1
        moves = [sign * (p - entry) / risk for p in prices]
        out = {"best_r": round(max(moves), 2), "worst_r": round(min(moves), 2), "samples": len(prices)}
        if tp1 is not None:
            out["target_hit"] = any(sign * (p - tp1) >= 0 for p in prices)
        return out

    @staticmethod
    def _extras(raw):
        try:
            d = json.loads(raw) if isinstance(raw, str) and raw.strip().startswith("{") else {}
        except ValueError:
            d = {}
        return d if isinstance(d, dict) else {}

    def _flags(self, rows):
        """Behaviour flags across the whole sheet (they depend on neighbouring trades)."""
        seq = sorted([r for r in rows if r["origin"] != "Desk paper"], key=lambda r: r["_t_open"] or 0)
        groups = {}
        for r in seq:
            if r["_t_open"]:
                day = datetime.fromtimestamp(r["_t_open"], timezone.utc).date().isoformat()
                groups.setdefault((r["account"], day, r["session"]), []).append(r)
        counts = sorted(len(g) for g in groups.values())
        median = counts[len(counts) // 2] if counts else 0
        busy = {k for k, g in groups.items() if len(g) >= 3 and len(g) > 2 * median}
        for i, r in enumerate(seq):
            flags = []
            for p in seq[:i]:
                if p["instrument"] == r["instrument"] and p["_t_close"] and r["_t_open"] \
                        and 0 <= r["_t_open"] - p["_t_close"] <= REVENGE_WINDOW \
                        and (p["net"] or 0) < 0 and (r["volume"] or 0) >= (p["volume"] or 0):
                    flags.append("Revenge")
                    break
            snap, side = r["_snap"], r["_side"]
            if snap and snap.get("dir") == side and r["entry"] is not None:
                d_entry, d_sl = _num(snap.get("entry")), _num(snap.get("sl"))
                if d_entry is not None and d_sl is not None and abs(d_entry - d_sl) > 0:
                    beyond = (r["entry"] - d_entry) if side == "LONG" else (d_entry - r["entry"])
                    if beyond > abs(d_entry - d_sl):
                        flags.append("FOMO")
            if r["_t_open"]:
                day = datetime.fromtimestamp(r["_t_open"], timezone.utc).date().isoformat()
                if (r["account"], day, r["session"]) in busy:
                    flags.append("Overtrading")
            if r["setup"].startswith("Against desk"):
                flags.append("Against desk")
            first, last = r["_stop_first"], (r["_stop_last"] if r["_stop_last"] is not None else r["stop"])
            if first is not None and last is not None and \
                    ((side == "LONG" and last < first) or (side == "SHORT" and last > first)):
                flags.append("Moved stop")
            if (r["net"] or 0) > 0 and r["r"] is not None and r["r"] < 0.5 and r["path"].get("target_hit") is False:
                flags.append("Early exit")
            if r["status"] == "No stop used":
                flags.append("No stop")
            r["flags"] = flags
            r["emotion_suggest"] = next((e for f, e in FLAG_EMOTION if f in flags), "")

    # ------------------------------------------------------------ draft notes
    def draft_notes(self, r):
        """A one-paragraph review draft from facts the desk recorded. Never claims what the trader felt."""
        dp = r.get("dp") if isinstance(r.get("dp"), int) else 2
        f = lambda v: "" if v is None else f"{v:,.{dp}f}"
        side = "Long" if r["side"] in ("BUY", "LONG") else "Short"
        parts = [f"{side} {r['instrument']}" + (f" in {r['session']}" if r["session"] else "") + "."]
        snap = r.get("_snap")
        if snap and snap.get("dir") in ("LONG", "SHORT"):
            rel = "with" if r["setup"].startswith("With") else "against"
            parts.append(f"Taken {rel} the desk's {snap['dir']} {snap.get('grade') or ''} call".replace("  ", " ").rstrip()
                         + (f" (desk entry {f(_num(snap.get('entry')))}, stop {f(_num(snap.get('sl')))})." if snap.get("entry") else "."))
        elif snap:
            parts.append("The desk was neutral at entry.")
        held = (r["_t_close"] - r["_t_open"]) if r["_t_close"] and r["_t_open"] else None
        if held is not None:
            h, mnt = divmod(int(held // 60), 60)
            parts.append(f"Held {h} h {mnt} min." if h else f"Held {mnt} min.")
        pth = r.get("path") or {}
        if "best_r" in pth:
            parts.append(f"Best {pth['best_r']:+.1f}R, worst {pth['worst_r']:+.1f}R on the desk's 5-minute prices.")
        if r["exit"] is not None:
            res = f"Closed at {f(r['exit'])}" + (f" for {r['r']:+.2f}R." if r["r"] is not None else ".")
            if pth.get("target_hit") is True:
                res += " The desk's first target was reached."
            elif pth.get("target_hit") is False:
                res += " The desk's first target was not reached."
            parts.append(res)
        flags = r.get("flags") or []
        if "Revenge" in flags:
            lesson = "This came soon after a loss on the same instrument; a cooling-off rule would protect the account."
        elif "Moved stop" in flags:
            lesson = "The stop was widened during the trade, which raised the risk; keep the original stop."
        elif "No stop" in flags:
            lesson = "No stop was used, so the risk was open-ended; set the stop before entry."
        elif "FOMO" in flags:
            lesson = "Entry was well beyond the desk's level; wait for price to come back to the plan."
        elif "Early exit" in flags:
            lesson = "Closed before the target; consider holding to the desk target or trailing the stop."
        elif "Against desk" in flags and (r["net"] or 0) < 0:
            lesson = "Trading against the desk cost this one; wait for alignment."
        elif r["setup"].startswith("With desk") and (r["net"] or 0) > 0:
            lesson = "Process worked: aligned with the desk and managed to plan."
        else:
            lesson = "Note what you saw before entry and whether you would take it again."
        parts.append("Lesson to consider: " + lesson)
        return " ".join(parts)

    def draft_with_claude(self, trade_id):
        """Rewrite one draft with Claude when a key is set; the template draft otherwise."""
        row = next((r for r in self.sheet(include_paper=True)["rows"] if r["id"] == trade_id), None)
        if row is None:
            raise ValueError("unknown trade")
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return {"ok": True, "draft": row["notes_draft"], "by": "template"}
        import urllib.request
        facts = {k: row.get(k) for k in ("instrument", "side", "session", "setup", "conditions", "entry", "stop",
                                          "exit", "r", "net", "currency", "flags", "path")}
        system = ("You write a trader's journal note from recorded facts. One short paragraph, plain British English, "
                  "no dashes, no hype. State what happened and end with one lesson to consider. Never claim how the "
                  "trader felt and never give a trading recommendation.")
        body = json.dumps({"model": os.environ.get("NORTHCHART_TEAM_MODEL",
                                                   os.environ.get("NORTHCHART_AGENT_MODEL", "claude-sonnet-5-5")),
                           "max_tokens": 300, "system": system,
                           "messages": [{"role": "user", "content": json.dumps(facts, default=str)}]}).encode()
        req = urllib.request.Request(os.environ.get("NORTHCHART_ANTHROPIC_URL", "https://api.anthropic.com/v1/messages"),
                                     data=body, method="POST", headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                                                         "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read())
            text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text").strip()
            text = text.replace("—", ", ").replace("–", ", ")
            return {"ok": True, "draft": text or row["notes_draft"], "by": "claude" if text else "template"}
        except Exception:
            return {"ok": True, "draft": row["notes_draft"], "by": "template"}

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
        fills = {f.get("fill_id"): f for f in tt.read("fills")}
        for t in tt.read("trades"):
            paper = t.get("account_label") == tt.PAPER_LABEL or t.get("account_type") == "PAPER" \
                and "paper engine" in (t.get("firm_or_broker") or "").lower()
            if paper and not include_paper:
                continue
            tid = t.get("trade_id")
            fix, m = corr.get(tid, {}), self.meta(tid)
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
            lessons = (t.get("lessons") or "").lower()
            if "stop_loss" in fix or m.get("stop_by") == "You" or lessons.startswith("stop entered in the sheet"):
                stop_by = "You"
            elif m.get("stop_by") or "desk stop" in lessons:
                stop_by = m.get("stop_by") or "Desk"
            else:
                stop_by = "Broker"
            notes = fix.get("lessons", t.get("lessons") or "")
            if "lessons" not in fix and any(notes.lower().startswith(x) for x in MACHINE_NOTES):
                notes = ""
            prov = {"stop_by": stop_by, "emotion": r.get("emotional_state") or "",
                    "notes": notes, "raw": (fills.get(tid) or {}).get("raw")}
            rows.append(self._row(r, "Complete", risk, rmult, net, "Desk paper" if paper else "Journal", prov, m))
        for f in tt.pending_fills():
            e = self.enrich(f)
            m = self.meta(f.get("fill_id"))
            net = _num(f.get("net_pl"))
            if net is None and _num(f.get("gross_pl")) is not None:
                net = _num(f.get("gross_pl")) - (_num(f.get("commission")) or 0) - (_num(f.get("swap")) or 0)
            no_stop = m.get("stop_mode") == "none"
            r = {"trade_id": f.get("fill_id"), "account_label": f.get("account_label"),
                 "account_type": f.get("account_type"), "instrument": f.get("instrument"),
                 "direction": f.get("direction"), "entry_time_utc": f.get("open_time_utc"),
                 "exit_time_utc": f.get("close_time_utc"), "entry_price": f.get("entry_price"),
                 "exit_price": f.get("exit_price"), "volume": f.get("volume"),
                 "stop_loss": "" if no_stop else (e.get("stop") or ""),
                 "currency": f.get("currency"), "session": e.get("session"), "setup": e.get("alignment"),
                 "market_conditions": e.get("conditions"),
                 "firm_or_broker": f"{f.get('broker')} via {f.get('source')}"}
            prov = {"stop_by": "" if no_stop or e.get("stop") is None else
                    ("Desk" if e.get("stop_source") == "desk stop at entry" else "Broker"),
                    "emotion": m.get("emotion") or "", "notes": m.get("notes") or "", "raw": f.get("raw")}
            rows.append(self._row(r, "No stop used" if no_stop else "Needs stop", None if no_stop else e.get("risk"),
                                  None, net, "Inbox", prov, m))
        self._flags(rows)
        for r in rows:
            r["notes_draft"] = self.draft_notes(r)
            if not r["notes"]:
                r["notes_by"] = "Draft"
            stop_ok = r["status"] in ("Complete", "No stop used")
            r["review"] = "Reviewed" if r["_m"].get("reviewed_at") or (
                stop_ok and r["emotion_by"] == "You" and r["notes_by"] == "You") else "To review"
            if r["origin"] == "Desk paper":
                r["review"] = ""
            for k in [k for k in r if k.startswith("_")]:
                del r[k]
        rows.sort(key=lambda x: x["opened"] or "", reverse=True)
        return {"rows": rows[:limit], "stats": self._stats(rows), "emotions": list(EMOTIONS),
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def _row(self, r, status, risk, rmult, net, origin, prov, m):
        sym = self.symbol_of(r.get("instrument"))
        dp = (self.symbols().get(sym) or {}).get("dp")
        side = "LONG" if str(r.get("direction")).upper() in ("BUY", "LONG") else "SHORT"
        t_open, t_close = _epoch(r.get("entry_time_utc")), _epoch(r.get("exit_time_utc"))
        entry, stop, exit_ = _num(r.get("entry_price")), _num(r.get("stop_loss")), _num(r.get("exit_price"))
        snap = self.desk_at(sym, t_open)
        ex = self._extras(prov.get("raw"))
        first = _num(ex.get("stop_initial") if "stop_initial" in ex else ex.get("initial_stop"))
        path_stop = first if first is not None else stop
        emotion = prov.get("emotion") or ""
        return {"id": r.get("trade_id"), "status": status, "origin": origin, "dp": dp,
                "instrument_raw": r.get("instrument") or "",
                "opened": r.get("entry_time_utc") or "", "closed": r.get("exit_time_utc") or "",
                "account": r.get("account_label") or r.get("account_type") or "", "type": r.get("account_type") or "",
                "instrument": sym or r.get("instrument") or "", "side": r.get("direction") or "",
                "volume": _num(r.get("volume")), "entry": entry, "stop": stop, "stop_by": prov.get("stop_by") or "",
                "stop_first": first, "exit": exit_, "risk": risk, "net": net, "currency": r.get("currency") or "",
                "r": rmult, "session": r.get("session") or "", "setup": r.get("setup") or "",
                "conditions": r.get("market_conditions") or "",
                "emotion": emotion, "emotion_by": "You" if emotion else "",
                "notes": prov.get("notes") or "", "notes_by": "You" if prov.get("notes") else "",
                "source": r.get("firm_or_broker") or "",
                "path": self.path(sym, side, entry, path_stop, t_open, t_close, exit_,
                                  _num((snap or {}).get("tp1")) if snap and snap.get("dir") == side else None),
                "_t_open": t_open, "_t_close": t_close, "_snap": snap, "_side": side, "_stop_first": first,
                "_stop_last": _num(ex.get("stop_final")), "_m": m}

    @staticmethod
    def _stats(rows):
        done = [r for r in rows if r["status"] == "Complete" and r["origin"] != "Desk paper"]
        live = [r for r in rows if r["origin"] != "Desk paper"]
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
        flags, by_emotion = {}, {}
        for r in live:
            for f in r.get("flags") or []:
                flags[f] = flags.get(f, 0) + 1
        for r in done:      # emotion stats use only what the trader confirmed
            if r["emotion_by"] == "You" and isinstance(r["r"], (int, float)):
                b = by_emotion.setdefault(r["emotion"], [0, 0.0])
                b[0] += 1
                b[1] += r["r"]
        return {"trades": len(done), "needs_stop": sum(1 for r in rows if r["status"] == "Needs stop"),
                "no_stop": sum(1 for r in rows if r["status"] == "No stop used"),
                "to_review": sum(1 for r in live if r["review"] == "To review"),
                "win_rate": round(100 * len(wins) / len(rs), 1) if rs else None,
                "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
                "total_r": round(sum(rs), 2) if rs else None,
                "best_r": max(rs) if rs else None, "worst_r": min(rs) if rs else None,
                "net_by_currency": by_ccy, "max_losing_streak": worst,
                "with_desk": sum(1 for r in done if r["setup"].startswith("With desk")),
                "against_desk": sum(1 for r in done if r["setup"].startswith("Against desk")),
                "flags": flags,
                "discipline_breaches": sum(1 for r in live if {"No stop", "Moved stop", "Revenge"} & set(r.get("flags") or [])),
                "by_emotion": {k: {"trades": v[0], "avg_r": round(v[1] / v[0], 2)} for k, v in by_emotion.items()}}

    # ------------------------------------------------------------ edits from the sheet
    def update(self, trade_id, field, value):
        """One cell edit. The trader's value always wins and is recorded as theirs."""
        tt = self.tracker()
        if tt is None:
            raise ValueError("tracker not installed")
        if field not in EDITABLE and field != "no_stop":
            raise ValueError("you can edit notes, emotion, setup or stop, or mark no stop used")
        value = str(value if value is not None else "").strip()[:1000]
        if field == "emotion" and value and value not in EMOTIONS:
            raise ValueError("emotion must be one of " + ", ".join(EMOTIONS))
        if field == "stop" and _num(value) is None:
            raise ValueError("stop must be a number")
        trades = {t.get("trade_id"): t for t in tt.read("trades")}
        if trade_id in trades:
            if field == "no_stop":
                raise ValueError("this trade is already in the journal with a stop")
            original = trades[trade_id].get(EDITABLE[field], "")
            tt.append("corrections", {"trade_id": trade_id, "field": EDITABLE[field], "original_value": original,
                                      "corrected_value": value, "reason": "edited in the journal sheet"})
            if field == "stop":
                self._set_meta(trade_id, stop_by="You")
            return {"ok": True, "journaled": False}
        pending = {f.get("fill_id"): f for f in tt.pending_fills()}
        if trade_id not in pending:
            raise ValueError("unknown trade")
        f = pending[trade_id]
        if field in ("emotion", "notes", "setup"):
            self._set_meta(trade_id, **{field: value or None})
            return {"ok": True, "journaled": False}
        if field == "no_stop":
            on = value.lower() in ("1", "true", "yes", "on")
            self._set_meta(trade_id, stop_mode="none" if on else None)
            return {"ok": True, "journaled": False}
        stop = _num(value)
        e = self.enrich({**f, "stop_loss": stop})
        if not e.get("risk"):
            raise ValueError("could not work out the money at risk for this trade")
        e["stop"] = stop
        m = self._set_meta(trade_id, stop_mode=None)
        self._journal(tt, f, e, "You", m)
        return {"ok": True, "journaled": True}

    def review(self, trade_id, body):
        """Confirm one trade from the review card: stop or no stop, emotion and notes, in one step."""
        body = body or {}
        out = {"ok": True, "journaled": False}
        if body.get("no_stop"):
            self.update(trade_id, "no_stop", "1")
        elif body.get("stop") not in (None, ""):
            cur = next((r for r in self.sheet(include_paper=True)["rows"] if r["id"] == trade_id), None)
            if cur is None:
                raise ValueError("unknown trade")
            if cur["stop"] is None or abs(float(cur["stop"]) - float(body["stop"])) > 1e-9 or cur["status"] != "Complete":
                out["journaled"] = self.update(trade_id, "stop", body["stop"]).get("journaled", False)
        for field in ("emotion", "notes"):
            if body.get(field) not in (None, ""):
                cur = next((r for r in self.sheet(include_paper=True)["rows"] if r["id"] == trade_id), None)
                if cur is None:
                    raise ValueError("unknown trade")
                if str(cur.get(field) or "") != str(body[field]).strip():
                    self.update(trade_id, field, body[field])
        self._set_meta(trade_id, reviewed_at=int(time.time()))
        return out

    def queue(self):
        rows = [r for r in self.sheet()["rows"] if r["review"] == "To review"]
        rows.sort(key=lambda r: r["closed"] or r["opened"] or "")
        return {"count": len(rows), "rows": rows}
