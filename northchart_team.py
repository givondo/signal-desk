"""The desk team: four specialist agents and a lead analyst.

Each specialist reads the same live tool output (snapshot, signal detail,
candles) from its own angle and returns a short report with a verdict. The
lead analyst then writes the answer from those reports, saying where the
specialists agree, where they disagree and what the risk manager allows.

Specialists work without an API key (rules over the engine's numbers). With
``ANTHROPIC_API_KEY`` set, each specialist is also a separate Claude call run
in parallel with its own brief, and the lead is a streamed Claude call; any
specialist or lead that fails falls back to its rule-based version.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

TEAM = [
    {"id": "market", "name": "Market analyst", "role": "Trend, momentum and structure across timeframes"},
    {"id": "macro", "name": "Macro analyst", "role": "Dollar, yields, volatility and cross-asset drivers"},
    {"id": "news", "name": "News analyst", "role": "Headlines and sentiment"},
    {"id": "risk", "name": "Risk manager", "role": "Whether the desk's rules allow a trade, and how big"},
]

VERDICTS = ("bullish", "bearish", "neutral", "clear", "blocked", "caution", "no read")


def _f(v, dp=None):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        dp = dp if dp is not None else (2 if abs(v) >= 100 else 3 if abs(v) >= 10 else 4)
        return f"{v:,.{dp}f}"
    return str(v)


def _dir_word(x, band=0.15):
    if x is None:
        return "neutral"
    return "bullish" if x > band else "bearish" if x < -band else "neutral"


def _score(d, key):
    for s in d.get("scores") or []:
        if s.get("k") == key:
            return s.get("v")
    return None


# ------------------------------------------------------------------ rule-based specialists

def market_analyst(s, d, candles):
    tech = d.get("tech_score")
    verdict = _dir_word(tech)
    reg = d.get("regime") or s.get("regime") or {}
    pts = [f"Composite technical score {_f(tech, 2)}; trend {_f(_score(d, 'TREND'))}, momentum "
           f"{_f(_score(d, 'MOMENTUM'))}, structure {_f(_score(d, 'STRUCTURE'))} (each -100 to +100)."]
    if isinstance(reg, dict) and reg:
        pts.append(f"Regime {reg.get('state', 'n/a').lower()}, ADX {_f(reg.get('adx'), 1)}, volatility "
                   f"{str(reg.get('vol', 'n/a')).lower()}.")
    pts.append(f"Timeframes agree {s.get('mtf_agreement')}% with the {str(s.get('direction')).lower()} bias.")
    m = d.get("matrix") or []
    if m:
        up = [r["tf"] for r in m if (r.get("trend") or {}).get("s") == 1]
        dn = [r["tf"] for r in m if (r.get("trend") or {}).get("s") == -1]
        if up or dn:
            pts.append(f"Trend up on {', '.join(up) or 'none'}; down on {', '.join(dn) or 'none'}.")
    ema = d.get("ema") or {}
    px = s.get("price")
    if isinstance(px, (int, float)) and isinstance(ema.get("h1_200"), (int, float)):
        pts.append(f"Price is {'above' if px > ema['h1_200'] else 'below'} the 1h EMA200 ({_f(ema['h1_200'], s.get('dp'))}).")
    bars = (candles or {}).get("bars") or []
    if len(bars) >= 20:
        hi = max(b["h"] for b in bars[-20:])
        lo = min(b["l"] for b in bars[-20:])
        pts.append(f"Last 5 hours range {_f(lo, s.get('dp'))} to {_f(hi, s.get('dp'))}.")
    conf = min(95, int(abs(tech or 0) * 100) + 20)
    return {"verdict": verdict, "confidence": conf, "points": pts,
            "summary": f"{verdict.title()} technically, {s.get('mtf_agreement')}% timeframe agreement"}


def macro_analyst(s, d, candles):
    m = d.get("macro") or {}
    sc = m.get("score", d.get("macro_score"))
    verdict = _dir_word(sc)
    rows = sorted([r for r in m.get("rows") or [] if r.get("impact")], key=lambda r: -(r.get("strength") or 0))
    pts = [f"Macro score {_f(sc, 2)} for this instrument (positive supports a rise)."]
    for r in rows[:3]:
        unit = "bp" if r.get("unit") == "bps" else "%"
        pts.append(f"{r.get('name')} {r.get('chg'):+}{unit}: {r.get('note', '')}")
    if not rows:
        pts.append("No macro driver is moving enough to matter.")
    return {"verdict": verdict, "confidence": min(90, int(abs(sc or 0) * 100) + 20), "points": pts,
            "summary": f"Macro {verdict}, score {_f(sc, 2)}"}


def news_analyst(s, d, candles):
    ns = d.get("news_sentiment") or {}
    heads = [h.get("title") for h in d.get("headlines") or [] if h.get("title")]
    n, bull, bear = ns.get("n", 0) or 0, ns.get("bull", 0) or 0, ns.get("bear", 0) or 0
    if not n and not heads:
        return {"verdict": "no read", "confidence": 0, "points": ["No recent headlines for this instrument."],
                "summary": "No news to weigh"}
    verdict = "bullish" if bull > bear else "bearish" if bear > bull else "neutral"
    pts = [f"{n} scored headlines: {bull} positive, {bear} negative."] if n else []
    pts += [f"“{h}”" for h in heads[:3]]
    return {"verdict": verdict, "confidence": min(80, 20 + 10 * abs(bull - bear)), "points": pts,
            "summary": f"News {verdict} ({bull} up, {bear} down)" if n else "Headlines only, no score"}


def risk_manager(s, d, candles):
    da = d.get("desk_action") or s.get("desk_action") or {}
    gov = d.get("governor") or {}
    sess = s.get("session") or {}
    lv = d.get("levels") or {}
    warns = d.get("warnings") or []
    pts = []
    blocked = []
    if da.get("mode") == "CLOSED":
        blocked.append("market closed")
    if gov.get("halted"):
        blocked.append("desk daily stop hit")
    if not s.get("tradeable"):
        blocked.append("signal does not pass every filter")
    if isinstance(sess, dict) and sess.get("liq") in ("thin", "closed"):
        pts.append(f"Session {sess.get('name')}, liquidity {sess.get('liq')}.")
    sl, entry, tp1 = lv.get("sl"), lv.get("entry"), lv.get("tp1")
    if all(isinstance(x, (int, float)) for x in (sl, entry, tp1)) and entry != sl:
        rr = abs(tp1 - entry) / abs(entry - sl)
        pts.append(f"Stop {_f(abs(entry - sl), s.get('dp'))} from entry; TP1 pays {rr:.1f}R.")
        if rr < 1.5:
            pts.append("TP1 is under the desk's 1.5R minimum.")
    atr = d.get("atr_1h") or s.get("atr_1h")
    if isinstance(d.get("sl_dist"), (int, float)) and isinstance(atr, (int, float)) and atr:
        pts.append(f"Stop distance is {d['sl_dist'] / atr:.1f}x the 1h ATR.")
    if gov:
        pts.append(f"Desk day {gov.get('day_r', 0):+}R of a -{gov.get('daily_stop_r')}R stop; "
                   f"{gov.get('open', 0)} of {gov.get('max_open')} tickets open.")
    pts += [f"Warning: {w}" for w in warns[:3]]
    eng = d.get("engine") or {}
    if eng.get("confluence_10") is not None:
        pts.append(f"Confluence {eng['confluence_10']}/10 against a publish threshold of "
                   f"{eng.get('publish_threshold', 7)}.")
    if blocked:
        return {"verdict": "blocked", "confidence": 90, "points": ["No trade: " + "; ".join(blocked) + "."] + pts,
                "summary": "No trade: " + blocked[0]}
    if warns:
        return {"verdict": "caution", "confidence": 60, "points": pts, "summary": f"Allowed with care: {warns[0]}"}
    return {"verdict": "clear", "confidence": 75, "points": pts, "summary": "Inside every desk limit"}


RULES = {"market": market_analyst, "macro": macro_analyst, "news": news_analyst, "risk": risk_manager}


# ------------------------------------------------------------------ lead analyst (rules)

def lead_report(sym, s, d, reports, question=None, copilot=None):
    dp = s.get("dp") if isinstance(s.get("dp"), int) else None
    name = s.get("sym_name") or sym
    bias = str(s.get("direction") or "NEUTRAL").upper()
    want = {"LONG": "bullish", "SHORT": "bearish"}.get(bias, "neutral")
    votes = {k: reports[k]["verdict"] for k in ("market", "macro", "news")
             if k in reports and reports[k]["verdict"] != "no read"}
    agree = [k for k, v in votes.items() if v == want and want != "neutral"]
    against = [k for k, v in votes.items() if v in ("bullish", "bearish") and v != want]
    risk = reports.get("risk", {})
    chg = s.get("change_pct")
    out = [f"**{name} ({sym}) {_f(s.get('price'), dp)}**"
           + (f", {'up' if chg > 0 else 'down'} {abs(chg):.2f}% today" if isinstance(chg, (int, float)) and chg
              else "") + f". Data as of {s.get('ts')}."]
    team = {"market": "market", "macro": "macro", "news": "news"}
    if want == "neutral":
        verdict = "The desk has **no directional bias**"
    else:
        verdict = (f"The desk leans **{bias}** ({s.get('confidence')}% confidence, grade {s.get('grade') or '-'})")
    if agree or against:
        verdict += (f"; {len(agree)} of {len(votes)} specialists agree"
                    + (f" ({', '.join(team[a] for a in agree)})" if agree else "")
                    + (f" and {', '.join(team[a] for a in against)} disagree" if against else "") + ".")
    else:
        verdict += "."
    out.append("\n" + verdict)
    rv = risk.get("verdict")
    if rv == "blocked":
        out.append(f"**Risk manager: no trade.** {risk.get('summary', '')[len('No trade: '):].capitalize()}.")
    elif rv == "caution":
        out.append(f"**Risk manager: allowed with care.** {risk.get('summary', '')}.")
    elif rv == "clear":
        out.append("**Risk manager: inside every desk limit.**")
    if copilot:
        out.append(f"\n{copilot}")
    labels = {"market": "Market", "macro": "Macro", "news": "News", "risk": "Risk"}
    out.append("\n#### What the team found")
    for k in ("market", "macro", "news", "risk"):
        r = reports.get(k)
        if not r:
            continue
        pts = r.get("points") or []
        out.append(f"- **{labels[k]}, {r['verdict']}:** " + (pts[0] if pts else r.get("summary", "")))
    lv = d.get("levels") or {}
    if lv.get("entry") is not None:
        tps = [lv.get(k) for k in ("tp1", "tp2", "tp3") if lv.get(k) is not None]
        out.append("\n#### Levels")
        out.append(f"- Entry {_f(lv.get('entry'), dp)} ({str(lv.get('model', 'n/a')).lower()}), stop "
                   f"{_f(lv.get('sl'), dp)}" + (", targets " + ", ".join(_f(t, dp) for t in tps) if tps else ""))
    rs = d.get("reasons") or {}
    inval = rs.get("invalidate") if isinstance(rs, dict) else None
    if inval:
        out.append("\n#### What would change the view")
        out.extend(f"- {x}" for x in inval[:3])
    out.append("\n_Research from the desk team, not personalised advice._")
    return "\n".join(out)


# ------------------------------------------------------------------ Claude-backed team

SPECIALIST_PROMPT = """You are the {name} on the Northcharts trading desk. Your remit: {role}.
You get the desk's live data for one instrument as JSON. Judge it only from your remit and only from the
numbers given; never invent a level or figure. Reply with JSON only, no prose around it:
{{"verdict": one of {verdicts}, "confidence": 0-100, "summary": "one line, max 14 words",
"points": ["2 to 4 short evidence points with the numbers"]}}
Use British English and no em or en dashes."""

LEAD_PROMPT = """You are the lead analyst on the Northcharts trading desk. Four specialists (market, macro, news,
risk) have each reported on one instrument. Write the answer to the user's question from their reports and the
live data only. Never invent a number.
Structure: one bold line with instrument, price and data time. Then the desk's view in one or two sentences,
saying how many specialists agree and naming any who disagree. Then the risk manager's ruling. Then a heading
'#### What the team found' with one bullet per specialist. Then levels if given and '#### What would change the
view'. End with the italic line '_Research from the desk team, not personalised advice._'
Under 230 words. British English. Never use em dashes or en dashes."""


def _payload(s, d, candles):
    bars = (candles or {}).get("bars") or []
    return {"snapshot": s, "detail": {k: v for k, v in d.items() if k not in ("matrix",)},
            "matrix": d.get("matrix"), "recent_bars": bars[-24:]}


def run_team(sym, s, d, candles, claude=None):
    """Run the four specialists in parallel. ``claude`` is an optional callable
    (system, user) -> str for a single non-streamed model reply.
    Yields ("start", agent) and ("done", agent, report) as each finishes."""
    data = _payload(s, d, candles)

    def one(agent):
        report = RULES[agent["id"]](s, d, candles)
        report["source"] = "rules"
        if claude:
            try:
                raw = claude(SPECIALIST_PROMPT.format(name=agent["name"], role=agent["role"],
                                                      verdicts=", ".join(VERDICTS)),
                             json.dumps({"instrument": sym, **data}, default=str)[:24000])
                txt = raw[raw.find("{"): raw.rfind("}") + 1]
                got = json.loads(txt)
                if got.get("verdict") in VERDICTS and isinstance(got.get("points"), list):
                    report = {"verdict": got["verdict"], "confidence": int(got.get("confidence") or 0),
                              "summary": str(got.get("summary") or "")[:140],
                              "points": [str(p)[:240] for p in got["points"][:4]], "source": "claude"}
            except Exception:
                pass   # keep the rule-based report
        return agent, report

    with ThreadPoolExecutor(max_workers=len(TEAM)) as pool:
        futures = [pool.submit(one, a) for a in TEAM]
        for f in as_completed(futures):      # report each specialist as soon as it finishes
            yield f.result()
