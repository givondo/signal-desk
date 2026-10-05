"""
Precifarm desk analyst — structured brief from live Signal Desk state only.
No invented prices; missing calendar/account → WATCHLIST ONLY where required.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

EAT = ZoneInfo("Africa/Nairobi")
LON = ZoneInfo("Europe/London")
NY = ZoneInfo("America/New_York")

PRIMARY = "XAUUSD"
SECONDARY_MAP = {
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "US30": "US30",
    "NAS100": "NAS100",
}

RISK_DEFAULTS = {
    "per_trade_pct": float(os.environ.get("DESK_RISK_PER_TRADE_PCT", "0.5")),
    "daily_loss_pct": float(os.environ.get("DESK_DAILY_LOSS_PCT", "2.0")),
    "max_spread_points": float(os.environ.get("DESK_MAX_SPREAD_POINTS", "35")),
    "contract_size": float(os.environ.get("DESK_XAU_CONTRACT_OZ", "100")),
    "account_balance": float(os.environ.get("DESK_ACCOUNT_BALANCE", "0")),
    "publish_threshold": int(os.environ.get("DESK_PUBLISH_THRESHOLD", "7")),
}

DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DESK_STATE_FILE = os.path.join(DATA_DIR, "precifarm_desk_state.json")


def _load_desk_state():
    try:
        with open(DESK_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"daily_losses_r": 0.0, "consecutive_losses_today": 0,
                "desk_closed_date": None, "signal_seq": 0}


def _save_desk_state(st):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DESK_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def eat_now() -> datetime:
    return datetime.now(EAT)


def _at_local(day: datetime, hour: int, minute: int, tz) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def session_clock_lines(now: datetime | None = None) -> list[str]:
    now = now or eat_now()
    today_eat = now.date()
    lon_open = _at_local(now, 8, 0, LON).astimezone(EAT)
    lon_close = _at_local(now, 16, 30, LON).astimezone(EAT)
    ny_open = _at_local(now, 9, 30, NY).astimezone(EAT)
    ny_close = _at_local(now, 16, 0, NY).astimezone(EAT)
    overlap_start = max(lon_open, ny_open)
    overlap_end = min(lon_close, ny_close)

    def delta_str(target: datetime) -> str:
        if now >= target:
            return "passed"
        d = target - now
        h, rem = divmod(int(d.total_seconds()), 3600)
        m = rem // 60
        return f"{h}h {m}m"

    active = "Off-hours (Asia/Pacific)"
    if overlap_start <= now <= overlap_end:
        active = "London/New York overlap (preferred)"
    elif lon_open <= now <= lon_close:
        active = "London"
    elif ny_open <= now <= ny_close:
        active = "New York"
    elif now.hour < 7:
        active = "Asia/Pacific (low liquidity for XAU)"

    return [
        f"Current EAT: {now.strftime('%Y-%m-%d %H:%M')} EAT",
        f"Active session: {active}",
        f"London open: {lon_open.strftime('%H:%M')} EAT ({delta_str(lon_open)})",
        f"New York open: {ny_open.strftime('%H:%M')} EAT ({delta_str(ny_open)})",
        f"Overlap window: {overlap_start.strftime('%H:%M')}–{overlap_end.strftime('%H:%M')} EAT "
        f"(ends in {delta_str(overlap_end) if now <= overlap_end else 'passed'})",
    ]


def _regime_sentence(sig) -> str:
    r = sig.get("regime") or {}
    st = r.get("state", "UNKNOWN")
    adx = r.get("adx")
    cc = sig.get("engine") or {}
    struct = cc.get("structure_s", 0)
    if st == "TRENDING":
        bias = sig.get("direction", "NEUTRAL")
        return (f"D1/H4 proxy: {st} (1h ADX {adx}), bias {bias}, "
                f"structure score {struct} — swings favour continuation unless broken.")
    if st == "RANGING":
        return f"D1/H4 proxy: ranging (ADX {adx}), structure {struct} — fade edges only (setup C)."
    return f"Regime {st} (ADX {adx}); treat as transitioning until ADX clarifies."


def _macro_gold_read(sig, macro_items) -> str:
    dxy = macro_items.get("dxy", {})
    y10 = macro_items.get("us10y", {})
    dc = dxy.get("chg_pct")
    yc = y10.get("chg_bps")
    ms = sig.get("macro_score", 0)
    parts = []
    if dc is not None:
        parts.append(f"DXY {dc:+.2f}%")
    if yc is not None:
        parts.append(f"US10Y {yc:+.1f}bp")
    head = " · ".join(parts) if parts else "Macro feed partial"
    agree = "aligned with typical inverse gold relationship" if ms * (
        1 if sig.get("direction") == "LONG" else -1 if sig.get("direction") == "SHORT" else 0
    ) >= 0 else "mixed or divergent vs spot — note only, not a standalone signal"
    if sig.get("direction") == "NEUTRAL":
        agree = "no directional gold bias to test agreement"
    return f"{head}. Gold desk macro score {ms:+.2f}; {agree}."


def _key_levels(sig, max_levels=5) -> list[tuple[str, str]]:
    px = sig.get("price")
    piv = sig.get("pivots") or {}
    levels = []
    for k, label in (("r1", "Prior pivot R1"), ("s1", "Prior pivot S1"),
                     ("p", "Pivot"), ("r2", "R2"), ("s2", "S2")):
        v = piv.get(k)
        if v is not None and px is not None:
            levels.append((f"{v}", f"{label} (TV classic)"))
    dh, dl = sig.get("day_high"), sig.get("day_low")
    if dh is not None:
        levels.append((f"{dh}", "Session/day high"))
    if dl is not None:
        levels.append((f"{dl}", "Session/day low"))
    if px is not None:
        handle = round(px / 25) * 25
        levels.append((f"{handle:.2f}", f"Nearest $25 handle"))
    out, seen = [], set()
    for price, why in levels:
        if price in seen:
            continue
        seen.add(price)
        out.append((price, why))
        if len(out) >= max_levels:
            break
    return out


def _infer_setup_type(sig) -> str | None:
    regime = (sig.get("regime") or {}).get("state")
    direction = sig.get("direction")
    if direction == "NEUTRAL":
        return None
    eng = sig.get("engine") or {}
    struct = eng.get("structure_s", 0)
    rec = next((m for m in (sig.get("models") or []) if m.get("recommended")), None)
    rname = rec.get("name") if rec else ""
    if regime == "RANGING":
        return "C"
    if abs(struct) >= 55 and direction == "LONG" and struct > 0:
        return "B"
    if abs(struct) >= 55 and direction == "SHORT" and struct < 0:
        return "B"
    if rname == "PULLBACK" or (regime in ("TRENDING", "MILD TREND") and rname != "BREAKOUT"):
        return "A"
    if rname == "BREAKOUT":
        return "D"
    return "A"


def confluence_score_10(sig, calendar_ok: bool, session_favourable: bool) -> tuple[int, dict]:
    """Precifarm 0–10 rubric from live signal only."""
    direction = sig.get("direction", "NEUTRAL")
    if direction == "NEUTRAL":
        return 0, {}
    br = {}
    # HTF 0-2
    htf = 0
    if sig.get("engine", {}).get("daily_ok"):
        htf += 1
    if sig.get("aligned"):
        htf += 1
    elif (sig.get("regime") or {}).get("state") == "TRENDING":
        htf += 1
    br["htf_bias"] = min(2, htf)
    # Level 0-2
    lv = 0
    struct = abs(sig.get("engine", {}).get("structure_s", 0))
    if struct >= 25:
        lv += 1
    if sig.get("levels"):
        lv += 1
    br["meaningful_level"] = min(2, lv)
    # Confirmation 0-2
    conf_pts = 0
    mtf = sig.get("mtf_agreement") or 0
    if mtf >= 65:
        conf_pts += 1
    if sig.get("tradeable") or mtf >= 55:
        conf_pts += 1
    br["confirmation"] = min(2, conf_pts)
    # Macro 0-1
    br["macro"] = 1 if sig.get("engine", {}).get("macro_ok") else 0
    # Clean path TP1 0-1
    rec = next((m for m in (sig.get("models") or []) if m.get("recommended")), None)
    rr = (rec or {}).get("rr") or 0
    br["path_tp1"] = 1 if rr >= 1.5 else 0
    # Session 0-1
    br["session"] = 1 if session_favourable else 0
    # Event blackout 0-1
    br["no_event_blackout"] = 1 if calendar_ok else 0
    total = sum(br.values())
    return total, br


def _position_size_lots(balance, risk_pct, sl_dist, contract_oz, px):
    if not balance or balance <= 0 or not sl_dist or sl_dist <= 0:
        return None, "Set DESK_ACCOUNT_BALANCE for lot calculation."
    risk_usd = balance * (risk_pct / 100.0)
    # XAU: ~$1 move per oz per lot (100 oz) → risk ≈ sl_dist * contract_oz
    risk_per_lot = sl_dist * contract_oz
    lots = risk_usd / risk_per_lot
    lots = round(max(0.01, min(lots, 50.0)), 2)
    calc = (f"{risk_usd:.2f} USD risk ({risk_pct}% of {balance:.0f}) ÷ "
            f"({sl_dist} × {contract_oz} oz/lot) ≈ {lots} lots")
    return lots, calc


def _spread_points(sig, cfg_spread_est) -> float:
    """Approx spread in 'points' using symbol dp (gold ~0.01 point = 1 pip)."""
    est = cfg_spread_est or 0.30
    dp = sig.get("dp", 2)
    unit = 10 ** (-dp)
    return round(est / unit, 1)


def build_desk_brief(sig, sym_cfg, *, secondary=None, calendar=None,
                     open_positions=None, account_balance=None,
                     risk_settings=None, extra_inputs=None) -> str:
    """Plain-text brief matching Precifarm section order."""
    risk = {**RISK_DEFAULTS, **(risk_settings or {})}
    if account_balance:
        risk["account_balance"] = float(account_balance)
    st = _load_desk_state()
    today = eat_now().strftime("%Y-%m-%d")
    if st.get("desk_closed_date") == today:
        return "1. DESK STATUS: CLOSED — daily loss limit reached.\n"

    missing = []
    epoch = sig.get("epoch") or 0
    if epoch and time.time() - epoch > 900:
        missing.append("price/signal stale (>15m)")
    if not calendar:
        missing.append("economic_calendar_next_48h")
    if not risk.get("account_balance"):
        missing.append("account_balance (optional for sizing)")

    threshold = risk["publish_threshold"]
    if st.get("consecutive_losses_today", 0) >= 2:
        threshold = max(threshold, 8)

    spread_pts = _spread_points(sig, sym_cfg.get("spread_est"))
    spread_block = spread_pts > risk["max_spread_points"]

    now = eat_now()
    session_lines = session_clock_lines(now)
    sess = sig.get("session") or {}
    session_fav = sess.get("liq") in ("high", "normal") and (
        "overlap" in session_lines[1].lower() or sess.get("name") == "LDN/NY OVERLAP"
    )

    score10, breakdown = confluence_score_10(sig, calendar_ok=bool(calendar),
                                             session_favourable=session_fav)
    direction = sig.get("direction", "NEUTRAL")
    stale = any("stale" in m for m in missing)
    tradeable = sig.get("tradeable") and not spread_block and not stale

    if missing and "economic_calendar_next_48h" in missing:
        desk_status = "WATCHLIST ONLY"
        status_reason = "Calendar not supplied — cannot certify event blackout."
    elif spread_block:
        desk_status = "WATCHLIST ONLY"
        status_reason = f"Spread ~{spread_pts} pts exceeds max {risk['max_spread_points']}."
    elif missing:
        desk_status = "WATCHLIST ONLY"
        status_reason = "Missing/stale: " + ", ".join(missing)
    elif score10 >= threshold and tradeable and direction != "NEUTRAL":
        desk_status = "OPEN"
        status_reason = f"Confluence {score10}/10 meets publish threshold {threshold}."
    else:
        desk_status = "WATCHLIST ONLY"
        status_reason = (f"Confluence {score10}/10 or filters below publish bar "
                         f"(need {threshold}+ and tradeable).")

    macro_dict = {}
    for row in (sig.get("macro") or {}).get("rows") or []:
        k = row.get("k")
        if not k:
            continue
        unit = row.get("unit")
        chg = row.get("chg")
        macro_dict[k] = {
            "chg_pct": chg if unit == "%" else None,
            "chg_bps": chg if unit == "bps" else None,
            "last": row.get("last"),
        }

    lines = []
    lines.append(f"1. DESK STATUS: {desk_status} — {status_reason}")
    lines.append("2. SESSION CLOCK:")
    lines.extend("   " + s for s in session_lines)

    brief_words = _regime_sentence(sig) + " " + _macro_gold_read(sig, macro_dict)
    lvls = _key_levels(sig)
    if lvls:
        brief_words += " Levels: " + "; ".join(f"{p} ({w})" for p, w in lvls[:3]) + "."
    if missing:
        brief_words += f" Event risk: UNVERIFIED ({', '.join(missing)})."
    else:
        brief_words += " Event risk: see supplied calendar."
    words = brief_words.split()
    lines.append("3. MARKET BRIEF: " + " ".join(words[:120]))

    lines.append("4. SIGNALS:")
    setup = _infer_setup_type(sig)
    if desk_status == "OPEN" and setup and sig.get("levels"):
        st["signal_seq"] = st.get("signal_seq", 0) + 1
        sid = f"{today}-{st['signal_seq']:03d}"
        _save_desk_state(st)
        lv = sig["levels"]
        rec = next((m for m in (sig.get("models") or []) if m.get("recommended")), lv)
        sl_d = sig.get("sl_dist") or 0
        lots, lcalc = _position_size_lots(
            risk["account_balance"], risk["per_trade_pct"], sl_d,
            risk["contract_size"], sig.get("price"))
        order = "Market" if rec.get("name") == "MARKET" else "Buy Limit" if direction == "LONG" else "Sell Limit"
        br_s = ", ".join(f"{k}={v}" for k, v in breakdown.items())
        valid_until = (now + timedelta(hours=4)).strftime("%H:%M EAT")
        lines.append(f"""
   SIGNAL ID: {sid}
   Instrument: XAU/USD
   Direction: {"BUY" if direction == "LONG" else "SELL"}
   Setup type: {setup}
   Order type: {order}
   Entry: {lv.get('entry')} (primary model {lv.get('model', rec.get('name'))})
   Stop loss: {lv.get('sl')} ({sl_d} price distance)
   TP1: {lv.get('tp1')} (1R) | TP2: {lv.get('tp2')} (2R)
   Position size: {lots if lots else 'n/a'} lots — {lcalc}
   Confluence score: {score10}/10 ({br_s})
   Valid until: {valid_until}
   Invalidation: {sig.get('reasons', {}).get('invalidate', ['Composite re-enters neutral'])[0]}
   Thesis: {sig.get('sym_name')} {direction} with MTF {sig.get('mtf_agreement')}% and grade {sig.get('grade')}. Macro score {sig.get('macro_score'):+.2f}.
   Management: 50% at TP1, stop to entry+spread; trail M15 structure after TP1; time stop if +0.5R not reached within hold window; flatten into unscheduled high-impact if calendar later shows conflict.""")
    else:
        lines.append("   (none published)")

    lines.append("5. WATCHLIST:")
    if 5 <= score10 < threshold or (direction != "NEUTRAL" and not tradeable):
        trig = []
        if score10 < threshold:
            trig.append(f"confluence reaches {threshold}/10")
        if not sig.get("engine", {}).get("daily_ok"):
            trig.append("daily rating aligns with bias")
        if (sig.get("mtf_agreement") or 0) < 65:
            trig.append("MTF agreement ≥65%")
        if not sig.get("tradeable"):
            trig.append("desk tradeable flag ON (regime/quality/chop)")
        lines.append("   XAU/USD — upgrade if: " + "; ".join(trig) or "filters clear.")
    else:
        lines.append("   (empty)")

    lines.append("6. NO-TRADE NOTES:")
    if desk_status != "OPEN" or direction == "NEUTRAL":
        lines.append(f"   {status_reason if direction == 'NEUTRAL' else status_reason}")
    else:
        lines.append("   Signal published above.")

    lines.append("7. REVIEW OF OPEN POSITIONS:")
    if open_positions:
        lines.append(f"   {open_positions}")
    else:
        lines.append("   No open_positions input — supply from MT5 for hold/adjust/close review.")

    if secondary:
        lines.append("")
        lines.append("(Secondary symbols requested — run separate brief with sym= for each.)")

    disclaimer_path = os.path.join(DATA_DIR, "desk_disclaimer_date.txt")
    try:
        with open(disclaimer_path, "r", encoding="utf-8") as f:
            last = f.read().strip()
    except Exception:
        last = ""
    if last != today:
        lines.append("")
        lines.append("Analysis for educational and decision-support purposes; not financial "
                       "advice. Trading leveraged products carries substantial risk of loss.")
        try:
            with open(disclaimer_path, "w", encoding="utf-8") as f:
                f.write(today)
        except Exception:
            pass

    return "\n".join(lines)
