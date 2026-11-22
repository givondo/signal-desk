"""Portfolio router for Signal Desk: one signal, one sized ticket per account.

Route and gate (decision D-011). A desk signal is checked against every account
in the live/prop/practice portfolio. Each account gets its own answer:

  TICKET   all checks pass and the account is ACTIVE: place it by hand
  WATCH    desk mode is PREPARE: levels staged, no entry yet
  DRY_RUN  checks pass but the account is PLANNED or PAUSED (what it would be)
  BLOCKED  a rule failed: the reasons say which

Nothing here places, modifies or cancels an order, and nothing ever will
without a new decision entry. Every route and every TAKEN/SKIPPED/MISSED answer
is logged, so the Trading OS Signals sheet can show signal quality separately
from execution.

Account settings live in DATA_DIR/trading_os_data/router/accounts.json (private,
never in the repo). trading_os/router_accounts.example.json is the template.

Optional alerts: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in
/etc/signaldesk.env on the server and new EXECUTE signals are sent with the
per-account lot sizes.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import trading_tracker as tt

ps, dc = tt.ps, tt.dc
HERE = Path(__file__).resolve().parent
EXAMPLE = HERE / "trading_os" / "router_accounts.example.json"
EAT = timezone(timedelta(hours=3))
TYPES = {"PERSONAL", "PROP", "PAPER", "DEMO"}
STATUSES = {"ACTIVE", "PLANNED", "PAUSED", "CLOSED"}
EDITABLE = {"enabled", "status", "risk_pct", "equity", "day_start_balance", "week_start_balance",
            "initial_balance", "highest_eod_balance", "currency", "conversion_rate", "notes"}
_lock = threading.Lock()
_alerted: dict[str, float] = {}


class RouterError(ValueError):
    pass


# ----------------------------------------------------------------- storage
def router_dir() -> Path:
    d = tt.data_root() / "router"
    d.mkdir(parents=True, exist_ok=True)
    return d


def accounts_path() -> Path:
    return router_dir() / "accounts.json"


def load_accounts() -> list[dict]:
    p = accounts_path()
    if not p.exists():
        p.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    data = json.loads(p.read_text(encoding="utf-8"))
    accounts = data.get("accounts", data) if isinstance(data, dict) else data
    for a in accounts:
        validate_account(a)
    return accounts


def save_accounts(accounts: list[dict]) -> None:
    labels = [a.get("label") for a in accounts]
    if len(set(labels)) != len(labels):
        raise RouterError("account labels must be unique")
    for a in accounts:
        validate_account(a)
    tmp = accounts_path().with_suffix(".tmp")
    tmp.write_text(json.dumps({"accounts": accounts}, indent=2), encoding="utf-8")
    tmp.replace(accounts_path())


def validate_account(a: dict) -> None:
    if not isinstance(a, dict) or not a.get("label"):
        raise RouterError("every account needs a label")
    if a.get("type") not in TYPES:
        raise RouterError(f"{a['label']}: type must be one of {sorted(TYPES)}")
    if a.get("status", "PLANNED") not in STATUSES:
        raise RouterError(f"{a['label']}: status must be one of {sorted(STATUSES)}")
    if a["type"] == "PROP" and a.get("rules_preset") not in dc.PRESETS:
        raise RouterError(f"{a['label']}: rules_preset must be one of {sorted(dc.PRESETS)}")


def update_account(label: str, fields: dict) -> dict:
    with _lock:
        accounts = load_accounts()
        acc = next((a for a in accounts if a["label"] == label), None)
        if acc is None:
            raise RouterError("unknown account")
        bad = sorted(set(fields) - EDITABLE)
        if bad:
            raise RouterError("fields not editable here: " + ", ".join(bad))
        for k, v in fields.items():
            if k in {"risk_pct", "equity", "day_start_balance", "week_start_balance", "initial_balance",
                     "highest_eod_balance", "conversion_rate"} and v not in (None, ""):
                v = float(v)
                if v <= 0:
                    raise RouterError(f"{k} must be positive")
            acc[k] = None if v == "" else v
        if {"equity", "day_start_balance"} & set(fields):
            acc["state_as_of"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_accounts(accounts)
        return acc


def _log(name: str, row: dict) -> None:
    with _lock, open(router_dir() / name, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _read_log(name: str, limit: int = 500) -> list[dict]:
    p = router_dir() / name
    if not p.exists():
        return []
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return rows[-limit:]


# ----------------------------------------------------------------- signals
def from_desk(sym: str, sig: dict) -> dict:
    """Normalise a Signal Desk /api/signal payload."""
    if not sig or sig.get("status") != "ok":
        raise RouterError(f"no live signal for {sym}")
    lv = sig.get("levels") or {}
    return {"symbol": sym, "direction": sig.get("direction"), "entry": lv.get("entry"), "stop": lv.get("sl"),
            "tp1": lv.get("tp1"), "tp2": lv.get("tp2"), "model": lv.get("model"),
            "mode": (sig.get("desk_action") or {}).get("mode"), "grade": sig.get("grade"),
            "confidence": sig.get("confidence"), "quality": sig.get("signal_quality"),
            "spread": sig.get("spread_est"), "price": sig.get("price"), "source": "desk"}


def normalise(signal: dict) -> dict:
    s = dict(signal or {})
    s["symbol"] = str(s.get("symbol") or "").upper()
    d = str(s.get("direction") or "").upper()
    s["direction"] = {"BUY": "LONG", "SELL": "SHORT"}.get(d, d)
    for k in ("entry", "stop", "tp1", "tp2", "spread"):
        if s.get(k) not in (None, ""):
            try:
                s[k] = float(s[k])
            except (TypeError, ValueError):
                raise RouterError(f"{k} must be a number") from None
        else:
            s[k] = None
    s["mode"] = str(s.get("mode") or "EXECUTE").upper()
    s.setdefault("source", "manual")
    return s


def route_id(s: dict) -> str:
    day = datetime.now(EAT).strftime("%Y-%m-%d")
    raw = f"{day}|{s['symbol']}|{s['direction']}|{s['entry']}|{s['stop']}"
    return day.replace("-", "") + "-" + hashlib.sha1(raw.encode()).hexdigest()[:8]


def signal_problems(s: dict) -> list[str]:
    out = []
    if s["direction"] not in ("LONG", "SHORT"):
        out.append(f"no direction ({s['direction'] or 'neutral'})")
    if not s["entry"] or not s["stop"]:
        out.append("signal has no entry and stop: every trade needs a stop")
    elif s["direction"] == "LONG" and s["stop"] >= s["entry"]:
        out.append("stop must be below entry for a LONG")
    elif s["direction"] == "SHORT" and s["stop"] <= s["entry"]:
        out.append("stop must be above entry for a SHORT")
    if s["mode"] not in ("EXECUTE", "PREPARE"):
        out.append(f"desk mode is {s['mode']}: no ticket")
    return out


# ----------------------------------------------------------------- account state
def _account_trades(label: str) -> list[dict]:
    return [t for t in tt.read("trades") if t.get("account_label") == label]


def _when(t: dict) -> datetime | None:
    v = t.get("exit_time_utc") or t.get("recorded_at_utc")
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def performance(label: str, now: datetime | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(EAT)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = day0 - timedelta(days=day0.weekday())
    today, week = [], 0.0
    for t in sorted(_account_trades(label), key=lambda r: _when(r) or datetime.min.replace(tzinfo=timezone.utc)):
        w = _when(t)
        pl = tt.ta.num(t.get("net_pl"), 0.0)
        if w and w >= week0:
            week += pl
        if w and w >= day0:
            today.append(pl)
    streak = 0
    for pl in reversed(today):
        if pl < 0:
            streak += 1
        else:
            break
    return {"day_pl": round(sum(today), 2), "week_pl": round(week, 2), "trades_today": len(today),
            "losses_in_a_row_today": streak}


def account_state(acc: dict) -> dict:
    """Latest snapshot for the account, else the balances kept in accounts.json."""
    snap = None
    for s in tt.read("snapshots"):
        if s.get("account_label") == acc["label"]:
            snap = s
    n = tt.ta.num
    if snap:
        return {"source": "snapshot " + (snap.get("recorded_at_utc") or ""),
                "initial": n(snap.get("initial_balance")), "equity": n(snap.get("equity")),
                "day_start": n(snap.get("day_start_balance")), "highest_eod": n(snap.get("highest_eod_balance"))}
    initial = acc.get("initial_balance")
    equity = acc.get("equity")
    day_start = acc.get("day_start_balance")
    source = "accounts.json " + (acc.get("state_as_of") or "")
    if acc["type"] == "PROP" and equity is None and acc.get("status") == "PLANNED" and initial:
        equity = day_start = initial
        source = "planned account: not purchased, untouched balance"
    return {"source": source.strip(), "initial": initial, "equity": equity,
            "day_start": day_start if day_start is not None else equity,
            "highest_eod": acc.get("highest_eod_balance")}


# ----------------------------------------------------------------- routing
def _spec(acc: dict, sym: str):
    base = ps.DEFAULT_SPECS.get(sym)
    o = (acc.get("specs") or {}).get(sym, {})
    contract = o.get("contract_size") or (base.contract_size if base else None)
    if not contract:
        raise RouterError(f"no contract specification for {sym}: add it under specs in accounts.json")
    return ps.InstrumentSpec(
        symbol=sym, contract_size=float(contract),
        quote_currency=o.get("quote_currency") or (base.quote_currency if base else "USD"),
        volume_step=float(o.get("volume_step") or (base.volume_step if base else 0.01)),
        volume_min=float(o.get("volume_min") or (base.volume_min if base else 0.01)),
        volume_max=float(o.get("volume_max") or (base.volume_max if base else 50.0)),
        commission_per_lot_round_turn=float(o.get("commission_per_lot") or 0.0))


def route_account(s: dict, acc: dict, limits: dict, now: datetime | None = None) -> dict:
    kind = "prop" if acc["type"] == "PROP" else "personal"
    lim = limits[kind]
    status = acc.get("status", "PLANNED")
    out = {"account": acc["label"], "type": acc["type"], "status": status,
           "firm_or_broker": acc.get("firm_or_broker"), "platform": acc.get("platform"),
           "broker_symbol": (acc.get("symbol_map") or {}).get(s["symbol"], s["symbol"]),
           "side": "BUY" if s["direction"] == "LONG" else "SELL", "entry": s["entry"], "stop": s["stop"],
           "tp1": s.get("tp1"), "tp2": s.get("tp2"), "lots": 0.0, "money_at_risk": 0.0,
           "risk_pct": None, "checks": [], "reasons": [], "notes": list(acc.get("rule_notes") or [])}
    reasons, checks = out["reasons"], out["checks"]

    if not acc.get("enabled", True):
        reasons.append("account switched off in the router")
    allowed = acc.get("symbols")
    if allowed and s["symbol"] not in allowed:
        reasons.append(f"{s['symbol']} is not on this account's list ({', '.join(allowed)})")

    st = account_state(acc)
    out["state"] = st
    perf = performance(acc["label"], now)
    out["today"] = perf
    if not st["equity"]:
        reasons.append("no equity recorded: update it on the Router page or add a snapshot")
        return _finish(out, s)

    if perf["losses_in_a_row_today"] >= lim["max_consecutive_losses_per_day"]:
        reasons.append(f"{perf['losses_in_a_row_today']} losses in a row today: stop for the day")
    else:
        checks.append(f"losses in a row today {perf['losses_in_a_row_today']} of {lim['max_consecutive_losses_per_day']}")

    risk = float(acc.get("risk_pct") or lim.get("max_risk_per_trade_pct") or lim.get("max_risk_per_trade_pct_of_initial"))
    if kind == "personal":
        cap = lim["max_risk_per_trade_pct"]
        risk = min(risk, cap)
        base = st["equity"]
        day_ref = st["day_start"] or base
        day_limit = day_ref * lim["daily_loss_limit_pct"] / 100
        if -perf["day_pl"] >= day_limit:
            reasons.append(f"daily loss {-perf['day_pl']:,.2f} has reached the {lim['daily_loss_limit_pct']}% limit ({day_limit:,.2f})")
        else:
            checks.append(f"daily loss {max(0.0, -perf['day_pl']):,.2f} of {day_limit:,.2f} allowed")
        week_ref = acc.get("week_start_balance") or day_ref
        week_limit = week_ref * lim["weekly_loss_limit_pct"] / 100
        if -perf["week_pl"] >= week_limit:
            reasons.append(f"weekly loss {-perf['week_pl']:,.2f} has reached the {lim['weekly_loss_limit_pct']}% limit ({week_limit:,.2f})")
        else:
            checks.append(f"weekly loss {max(0.0, -perf['week_pl']):,.2f} of {week_limit:,.2f} allowed")
        hard = lim["hard_cap_risk_per_trade_pct"]
    else:
        rules = dc.PRESETS[acc["rules_preset"]]
        out["notes"].append(f"{rules.firm} {rules.programme} (rules read {rules.verified_on}): {rules.notes}")
        if rules.kenya_eligible is False:
            reasons.append(f"{rules.firm} {rules.programme} is not open to Kenyan traders")
        if not st["initial"]:
            reasons.append("initial balance missing")
            return _finish(out, s)
        rep = dc.evaluate(rules, dc.AccountState(st["initial"], st["equity"], st["day_start"] or st["equity"],
                                                 highest_eod_balance=st["highest_eod"]), limits)
        out["drawdown"] = rep.__dict__
        out["notes"] += [w for w in rep.warnings if w not in out["notes"]]
        if rep.status in ("BREACHED", "CRITICAL"):
            reasons.append(f"account status {rep.status}: allowance used, stop and review")
        if rep.internal_stop_hit:
            reasons.append("internal daily stop reached: no more trades on this account today")
        if rep.max_used_fraction >= lim["stop_trading_account_when_max_loss_used_fraction"]:
            reasons.append("75% of the maximum loss used: account stopped for review")
        elif rep.max_used_fraction >= lim["reduce_risk_when_max_loss_used_fraction"]:
            risk = min(risk, lim["reduced_risk_per_trade_pct_of_initial"])
            checks.append(f"half the maximum loss used: risk cut to {risk}%")
        risk = min(risk, lim["max_risk_per_trade_pct_of_initial"])
        base = st["initial"]
        hard = lim["hard_cap_risk_per_trade_pct_of_initial"]
        checks.append(f"{rules.firm} status {rep.status}; {rep.remaining_allowance:,.2f} left before the {rep.binding_rule} floor")

    out["risk_pct"] = risk
    try:
        spread = float(acc.get("spread_override") or s.get("spread") or 0.0)
        sz = ps.size_position(equity=base, risk_pct=risk, entry=s["entry"], stop=s["stop"], spec=_spec(acc, s["symbol"]),
                              account_currency=acc.get("currency") or "USD", conversion_rate=acc.get("conversion_rate"),
                              spread=spread, slippage=float(acc.get("slippage") or 0.0), hard_cap_pct=hard,
                              account_kind=kind)
    except (ValueError, RouterError) as e:
        reasons.append(f"sizing refused: {e}")
        return _finish(out, s)
    out["lots"], out["money_at_risk"] = sz.lots, sz.expected_loss_at_stop
    out["risk_pct_actual"] = sz.risk_pct_of_equity_actual
    reasons += sz.reasons if not sz.accepted else []
    if kind == "prop" and sz.accepted:
        room = rep.equity - (rep.internal_daily_stop if rep.internal_daily_stop is not None else rep.binding_floor)
        if sz.expected_loss_at_stop > room:
            reasons.append(f"one stop-out ({sz.expected_loss_at_stop:,.2f}) would pass the internal daily stop (room {room:,.2f})")
        else:
            checks.append(f"one stop-out {sz.expected_loss_at_stop:,.2f} fits the internal daily room {room:,.2f}")
    checks.append(f"size {sz.lots} lots risks {sz.expected_loss_at_stop:,.2f} ({sz.risk_pct_of_equity_actual}%) at the stop, costs included")
    return _finish(out, s)


def _finish(out: dict, s: dict) -> dict:
    if out["reasons"]:
        out["decision"] = "BLOCKED"
    elif out["status"] != "ACTIVE":
        out["decision"] = "DRY_RUN"
    elif s["mode"] == "PREPARE":
        out["decision"] = "WATCH"
    else:
        out["decision"] = "TICKET"
    return out


def account_health(acc: dict, limits: dict | None = None, now: datetime | None = None) -> dict:
    """Limit usage for the gauges on the Router page: 0 is untouched, 1 is the limit."""
    limits = limits or ps.load_limits()
    kind = "prop" if acc["type"] == "PROP" else "personal"
    lim = limits[kind]
    st, perf = account_state(acc), performance(acc["label"], now)
    out = {"kind": kind, "equity": st["equity"], "today_pl": perf["day_pl"], "week_pl": perf["week_pl"],
           "trades_today": perf["trades_today"], "streak": perf["losses_in_a_row_today"],
           "streak_max": lim["max_consecutive_losses_per_day"], "daily_used": None, "max_used": None,
           "week_used": None, "status": "NO DATA" if not st["equity"] else "OK"}
    if not st["equity"]:
        return out
    if kind == "personal":
        day_ref = st["day_start"] or st["equity"]
        out["daily_used"] = max(0.0, -perf["day_pl"]) / (day_ref * lim["daily_loss_limit_pct"] / 100)
        week_ref = acc.get("week_start_balance") or day_ref
        out["week_used"] = max(0.0, -perf["week_pl"]) / (week_ref * lim["weekly_loss_limit_pct"] / 100)
        worst = max(out["daily_used"], out["week_used"], out["streak"] / out["streak_max"])
        out["status"] = "STOPPED" if worst >= 1 else "WARN" if worst >= 0.5 else "OK"
        return out
    rules = dc.PRESETS.get(acc.get("rules_preset"))
    if not rules or not st["initial"]:
        out["status"] = "NO DATA"
        return out
    rep = dc.evaluate(rules, dc.AccountState(st["initial"], st["equity"], st["day_start"] or st["equity"],
                                             highest_eod_balance=st["highest_eod"]), limits)
    out.update(daily_used=rep.daily_used_fraction, max_used=rep.max_used_fraction, status=rep.status,
               remaining=rep.remaining_allowance, binding_rule=rep.binding_rule)
    if rep.internal_stop_hit or out["streak"] >= out["streak_max"]:
        out["status"] = "STOPPED" if rep.status == "OK" else rep.status
    return out


def route(signal: dict, accounts: list[dict] | None = None, now: datetime | None = None, log: bool = True) -> dict:
    s = normalise(signal)
    accounts = load_accounts() if accounts is None else accounts
    limits = ps.load_limits()
    rid = route_id(s)
    problems = signal_problems(s)
    rows = []
    for acc in accounts:
        if problems:
            rows.append({"account": acc["label"], "type": acc["type"], "status": acc.get("status"),
                         "decision": "BLOCKED", "reasons": list(problems), "checks": [], "notes": [], "lots": 0.0})
        else:
            rows.append(route_account(s, acc, limits, now))
    notes = ["D-011: route and gate only. The desk never sends orders; place each ticket yourself and mark it Taken or Skipped."]
    live_prop = [r["account"] for r in rows if r["type"] == "PROP" and r["decision"] in ("TICKET", "WATCH")]
    if len(live_prop) > 1:
        notes.append("Same trade on several prop accounts: check each firm's copy-trading and hedging rules before placing.")
    out = {"route_id": rid, "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "signal": s, "signal_problems": problems, "accounts": rows, "notes": notes,
           "limits_version": limits.get("version")}
    if log:
        _log("routes.jsonl", {"route_id": rid, "at_utc": out["at_utc"], "signal": s,
                              "decisions": {r["account"]: [r["decision"], r.get("lots", 0.0)] for r in rows}})
    return out


def record_decision(body: dict) -> dict:
    rid, label = str(body.get("route_id") or ""), str(body.get("account") or "")
    choice = str(body.get("decision") or "").upper()
    if choice not in ("TAKEN", "SKIPPED", "MISSED"):
        raise RouterError("decision must be TAKEN, SKIPPED or MISSED")
    if not any(r.get("route_id") == rid for r in _read_log("routes.jsonl", 5000)):
        raise RouterError("unknown route_id")
    if label not in {a["label"] for a in load_accounts()}:
        raise RouterError("unknown account")
    row = {"route_id": rid, "account": label, "decision": choice, "note": str(body.get("note") or "")[:500],
           "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _log("decisions.jsonl", row)
    return row


def signals_csv() -> bytes:
    """One row per routed signal, in the column order of the Trading OS Signals sheet."""
    import csv
    import io
    taken = {}
    for d in _read_log("decisions.jsonl", 100000):
        taken.setdefault(d["route_id"], []).append(f"{d['account']}:{d['decision']}")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Signal ID", "Time (UTC)", "Model", "Direction", "Grade", "Confidence", "Entry", "Stop", "Target",
                "Planned R:R", "Decision", "Outcome", "Result R", "Trade No.", "Notes"])
    seen = set()
    for r in _read_log("routes.jsonl", 100000):
        if r["route_id"] in seen:
            continue
        seen.add(r["route_id"])
        s = r["signal"]
        rr = ""
        if s.get("tp1") and s.get("entry") and s.get("stop") and s["entry"] != s["stop"]:
            rr = round(abs(s["tp1"] - s["entry"]) / abs(s["entry"] - s["stop"]), 2)
        conf = s.get("confidence")
        w.writerow([r["route_id"], r["at_utc"], s.get("model") or s.get("source"),
                    {"LONG": "BUY", "SHORT": "SELL"}.get(s.get("direction"), s.get("direction")),
                    s.get("grade") or "", "" if conf in (None, "") else conf / 100 if conf > 1 else conf,
                    s.get("entry"), s.get("stop"), s.get("tp1"), rr,
                    "; ".join(taken.get(r["route_id"], [])), "", "", "", s.get("symbol")])
    return buf.getvalue().encode("utf-8")


# ----------------------------------------------------------------- alerts
def telegram_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def alert_text(r: dict) -> str:
    s = r["signal"]
    lines = [f"Northcharts {s['symbol']} {s['direction']} ({s.get('grade') or '-'}, {s['mode']})",
             f"Entry {s['entry']}  Stop {s['stop']}  TP1 {s.get('tp1')}", ""]
    for a in r["accounts"]:
        if a["decision"] in ("TICKET", "WATCH", "DRY_RUN"):
            lines.append(f"{a['account']}: {a['decision']} {a['lots']} lots, risk {a.get('money_at_risk', 0):,.2f}")
        else:
            lines.append(f"{a['account']}: BLOCKED ({(a['reasons'] or ['-'])[0]})")
    lines += ["", "Place by hand; mark Taken or Skipped on /router. Route " + r["route_id"]]
    return "\n".join(lines)


def _send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=8).read()
    except Exception as e:  # alerts must never stop the desk
        print(f"router alert failed: {e}")


def on_signal(sym: str, sig: dict) -> None:
    """Called by the desk poller. Routes and alerts each new EXECUTE signal once."""
    try:
        if (sig or {}).get("status") != "ok" or (sig.get("desk_action") or {}).get("mode") != "EXECUTE":
            return
        s = normalise(from_desk(sym, sig))
        rid = route_id(s)
        if rid in _alerted:
            return
        _alerted[rid] = time.time()
        r = route(s)
        if telegram_configured() and any(a["decision"] != "BLOCKED" for a in r["accounts"]):
            threading.Thread(target=_send_telegram, args=(alert_text(r),), daemon=True).start()
    except Exception as e:
        print(f"router: {sym}: {e}")


# ----------------------------------------------------------------- HTTP
def handle(method: str, path: str, query: dict, body: dict | None, latest_signal=None):
    """Route a /api/router/* call. Returns (status, content_type, bytes, extra_headers)."""
    js = "application/json"

    def ok(obj):
        return 200, js, json.dumps(obj).encode(), {}
    try:
        if method == "GET" and path == "/api/router/accounts":
            accs = load_accounts()
            lim = ps.load_limits()
            return ok({"ok": True, "accounts": [{**a, "state": account_state(a), "today": performance(a["label"]),
                                                 "health": account_health(a, lim)} for a in accs],
                       "limits": ps.load_limits(), "presets": sorted(dc.PRESETS), "telegram": telegram_configured()})
        if method == "GET" and path == "/api/router/route":
            sym = (query.get("sym") or ["XAUUSD"])[0].upper()
            sig = latest_signal(sym) if latest_signal else None
            preview = (query.get("preview") or ["0"])[0] in ("1", "true")
            return ok({"ok": True, "preview": preview, **route(from_desk(sym, sig), log=not preview)})
        if method == "POST" and path == "/api/router/route":
            b = dict(body or {})
            if b.get("spread") in (None, "") and latest_signal:
                b["spread"] = (latest_signal(str(b.get("symbol") or "").upper()) or {}).get("spread_est")
            return ok({"ok": True, **route(b)})
        if method == "POST" and path == "/api/router/decision":
            return ok({"ok": True, "row": record_decision(body or {})})
        if method == "POST" and path == "/api/router/account":
            b = body or {}
            return ok({"ok": True, "account": update_account(str(b.get("label") or ""), b.get("fields") or {})})
        if method == "POST" and path == "/api/router/accounts":
            save_accounts((body or {}).get("accounts") or [])
            return ok({"ok": True, "accounts": load_accounts()})
        if method == "GET" and path == "/api/router/log":
            n = int((query.get("limit") or ["50"])[0])
            return ok({"ok": True, "routes": _read_log("routes.jsonl", n), "decisions": _read_log("decisions.jsonl", n)})
        if method == "GET" and path == "/api/router/signals.csv":
            return 200, "text/csv; charset=utf-8", signals_csv(), {
                "Content-Disposition": 'attachment; filename="Signal_Routes.csv"'}
        return 404, js, b'{"ok": false, "error": "not found"}', {}
    except (RouterError, tt.TrackerError, KeyError, ValueError) as e:
        return 400, js, json.dumps({"ok": False, "error": str(e)}).encode(), {}
