"""Northchart agent: the streaming brain behind the agent desk.

``DeskAgent.stream(question, symbol, history)`` yields events that the server
sends to the browser as Server-Sent Events:

    {"type": "start", "mode": "desk"|"claude", "symbol": ...}
    {"type": "tool", "id": n, "name": ..., "args": {...}, "status": "running"}
    {"type": "tool", "id": n, "status": "done", "ok": bool, "ms": int, "summary": str}
    {"type": "chart", "chart": {...}}          # candles, bars or line, drawn inline
    {"type": "card", "card": {...}}            # route table, metric grid
    {"type": "text", "delta": "..."}           # answer text, streamed in small pieces
    {"type": "suggestions", "items": [...]}
    {"type": "error", "message": ...}
    {"type": "done"}

Two modes, same tools:

* **desk** (default, no key needed): a deterministic planner picks the tools
  for the question, runs them, and writes the answer only from what they
  returned, so it never invents a level.
* **claude**: when ``ANTHROPIC_API_KEY`` is set, Claude runs the conversation
  with the Connect tools (read-only) and its words stream straight through.
  Any failure falls back to desk mode for that question.

Every tool call goes through ``Connect.run_tool`` so it appears in the
activity feed next to calls from ChatGPT and Claude connectors.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

import northchart_team as team

CLIENT = "Agent desk"
MAX_TURNS = 6
WORD_DELAY = float(os.environ.get("NORTHCHART_STREAM_DELAY", "0"))  # the browser paces the reveal

SYSTEM_PROMPT = """You are the Northchart desk agent, a research assistant on a multi-asset trading desk \
(gold first, plus FX majors, crypto, US indices and WTI oil). You work only through the Northchart tools, \
which are read-only: nothing you do can place, change or cancel an order.

Rules:
- Use the tools before stating any price, level, bias or statistic, and never invent a number they did not return.
- Give the data timestamp. If the desk action is CLOSED or quotes are stale, say so first.
- Be direct and numeric, like a senior desk analyst. Lead with the answer, then levels, then reasons and risks.
- When you discuss one instrument, call get_candles so the user sees a chart, and get_signal_detail for levels.
- This is research, not personalised investment advice. Do not tell the user what to buy or sell; describe \
what the desk's rules say and why.
- British English. Never use em dashes or en dashes; use commas, colons or full stops instead.
- Keep answers under about 250 words unless asked for more. Use short paragraphs, bold for key figures and \
'- ' bullets for lists."""

NAMES = {}  # symbol -> friendly words, filled from the engine on first use


def _fmt(v, dp=None):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        if dp is None:
            dp = 2 if abs(v) >= 100 else 4 if abs(v) < 10 else 3
        return f"{v:,.{dp}f}"
    return str(v)


def _clean(text: str) -> str:
    """House style for generated text: no em or en dashes."""
    return (text or "").replace(" — ", ", ").replace("—", ", ").replace(" – ", " to ").replace("–", "-")


class DeskAgent:
    def __init__(self, connect, symbols_fn):
        self.connect = connect
        self.symbols = symbols_fn
        self._seq = 0

    # ------------------------------------------------------------ helpers
    def _symbol_from(self, text: str, default: str) -> str:
        syms = self.symbols()
        up = re.sub(r"[^A-Z0-9 ]", " ", text.upper().replace("/", ""))
        for w in up.split():
            if w in syms:
                return w
        low = text.lower()
        aliases = {"gold": "XAUUSD", "silver": "XAGUSD", "bitcoin": "BTCUSD", "btc": "BTCUSD",
                   "ether": "ETHUSD", "ethereum": "ETHUSD", "eth": "ETHUSD", "solana": "SOLUSD",
                   "ripple": "XRPUSD", "xrp": "XRPUSD", "euro": "EURUSD", "cable": "GBPUSD", "pound": "GBPUSD",
                   "sterling": "GBPUSD", "aussie": "AUDUSD", "kiwi": "NZDUSD", "yen": "USDJPY",
                   "loonie": "USDCAD", "dow": "US30", "nasdaq": "NAS100", "oil": "USOIL", "crude": "USOIL",
                   "wti": "USOIL"}
        for word, sym in aliases.items():
            if re.search(rf"\b{word}\b", low) and sym in syms:
                return sym
        return default if default in syms else next(iter(syms))

    def _call(self, name, args):
        self._seq += 1
        tid = self._seq
        yield {"type": "tool", "id": tid, "name": name, "args": args, "status": "running"}
        t0 = time.time()
        ok, res = self.connect.run_tool(name, args, client=CLIENT)
        ms = int((time.time() - t0) * 1000)
        yield {"type": "tool", "id": tid, "name": name, "status": "done", "ok": ok, "ms": ms,
               "summary": self._summary(name, ok, res)}
        self._last = (ok, res)

    def _run(self, name, args):
        """Run a tool, yielding its events; the result lands in self._last."""
        yield from self._call(name, args)

    @staticmethod
    def _summary(name, ok, res):
        if not ok:
            return (res or {}).get("error", "failed")
        r = res or {}
        if name == "get_market_snapshot":
            return f"{r.get('sym')} {_fmt(r.get('price'), r.get('dp'))} {r.get('direction')} {r.get('grade') or ''}".strip()
        if name == "get_candles":
            return f"{len(r.get('bars', []))} bars, {r.get('interval')}"
        if name == "get_market_overview":
            return f"{len(r.get('symbols', []))} instruments"
        if name == "get_signal_detail":
            lv = r.get("levels") or {}
            return f"entry {_fmt(lv.get('entry'), r.get('dp'))}, stop {_fmt(lv.get('sl'), r.get('dp'))}"
        if name == "preview_signal_routing":
            return f"{len(r.get('accounts', []))} accounts"
        if name == "get_price_history":
            return f"{len(r.get('points', []))} points"
        return "ok"

    def _stream_text(self, text):
        text = _clean(text)
        for piece in re.findall(r"\S+\s*|\s+", text):
            yield {"type": "text", "delta": piece}
            if WORD_DELAY:
                time.sleep(WORD_DELAY)

    @staticmethod
    def _levels(detail):
        lv = (detail or {}).get("levels") or {}
        out = []
        for key, label, tone in (("entry", "Entry", "entry"), ("sl", "Stop", "stop"),
                                 ("tp1", "TP1", "target"), ("tp2", "TP2", "target")):
            if isinstance(lv.get(key), (int, float)):
                out.append({"label": label, "value": lv[key], "tone": tone})
        return out

    def _candle_chart(self, sym, candles, detail=None, snapshot=None):
        bars = (candles or {}).get("bars") or []
        if not bars:
            return None
        name = (snapshot or detail or {}).get("sym_name") or sym
        return {"kind": "candles", "title": f"{name} · 15m", "symbol": sym, "bars": bars,
                "levels": self._levels(detail), "price": (snapshot or {}).get("price"),
                "direction": (snapshot or detail or {}).get("direction"),
                "source": candles.get("source"), "dp": (snapshot or detail or {}).get("dp")}

    # ------------------------------------------------------------ public
    def stream(self, question: str, symbol: str = "XAUUSD", history=None):
        question = (question or "").strip()[:2000]
        sym = self._symbol_from(question, symbol)
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key and os.environ.get("NORTHCHART_AGENT_MODE", "auto") != "desk":
            yield {"type": "start", "mode": "claude", "symbol": sym}
            try:
                if self._intent(question) == "analyse":
                    yield from self._desk_analyse(question, sym, key=key)
                else:
                    yield from self._claude(question, sym, history or [], key)
                yield {"type": "done"}
                return
            except Exception as e:  # network, quota, bad key: answer from the desk instead
                yield {"type": "notice", "message": f"Claude unavailable ({type(e).__name__}); "
                                                    "answering with the desk engine."}
        yield {"type": "start", "mode": "desk", "symbol": sym}
        try:
            yield from self._desk(question, sym)
        except Exception as e:
            yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
        yield {"type": "done"}

    # ------------------------------------------------------------ desk mode
    def _intent(self, q):
        ql = q.lower()
        if re.search(r"\b(overview|all markets|markets|what'?s moving|scan|best setup|which (one|market|pair)s?|"
                     r"top setups?|rank)\b", ql):
            return "overview"
        if re.search(r"\b(route|routing|lots?|size|sizing|accounts?)\b", ql):
            return "route"
        if re.search(r"\bbrief\b", ql):
            return "brief"
        if re.search(r"\b(journal|my trades|review my|trade review|discipline|mistakes?)\b", ql):
            return "journal"
        if re.search(r"\b(portfolio|performance|prop|net worth)\b", ql):
            return "portfolio"
        if re.search(r"\b(chart|candles?|price action)\b", ql):
            return "chart"
        return "analyse"

    def _desk(self, q, sym):
        intent = self._intent(q)
        if intent == "overview":
            yield from self._desk_overview()
        elif intent == "route":
            yield from self._desk_route(sym)
        elif intent == "brief":
            yield from self._desk_brief(sym)
        elif intent == "portfolio":
            yield from self._desk_portfolio()
        elif intent == "journal":
            yield from self._desk_journal()
        else:
            yield from self._desk_analyse(q, sym, chart_only=(intent == "chart"))

    def _desk_overview(self):
        yield from self._run("get_market_overview", {})
        ok, ov = self._last
        if not ok:
            yield from self._stream_text(f"I could not read the market overview: {ov.get('error')}.")
            return
        rows = [r for r in ov.get("symbols", []) if r.get("status") == "ok"]
        bars = [{"label": r["sym"], "value": (r.get("confidence") or 0) * (1 if r.get("direction") == "LONG"
                                                                            else -1 if r.get("direction") == "SHORT"
                                                                            else 0),
                 "note": f"{r.get('direction') or '-'} {r.get('grade') or ''}".strip()} for r in rows]
        bars.sort(key=lambda b: -abs(b["value"]))
        yield {"type": "chart", "chart": {"kind": "bars", "title": "Directional conviction by instrument",
                                          "subtitle": "Confidence, signed by bias (long right, short left)",
                                          "bars": bars, "min": -100, "max": 100}}
        gov = ov.get("governor") or {}
        tradeable = [r for r in rows if r.get("tradeable")]
        modes = {r.get("mode") for r in rows}
        top = sorted(rows, key=lambda r: -(r.get("confidence") or 0))[:3]
        lines = [f"**{len(rows)} of {len(ov.get('symbols', []))} instruments live** at {ov.get('ts')}."]
        if modes == {"CLOSED"}:
            lines.append("All markets show **CLOSED**: weekend quotes are stale, so treat this as a map for the open, "
                         "not a list of trades.")
        if tradeable:
            lines.append("\n**Tradeable now:** " + ", ".join(
                f"{r['sym']} {r.get('direction')} ({r.get('grade')}, {r.get('confidence')}%)" for r in tradeable) + ".")
        else:
            lines.append("\n**Nothing is tradeable right now**: no instrument passes every filter.")
        if top:
            lines.append("\n**Strongest conviction:**")
            for r in top:
                lines.append(f"- **{r['sym']}** {r.get('direction')} at {r.get('confidence')}%, grade "
                             f"{r.get('grade') or '-'}, desk mode {r.get('mode') or '-'}")
        if gov:
            lines.append(f"\nDesk day **{gov.get('day_r', 0):+}R** against a daily stop of {gov.get('daily_stop_r')}R"
                         f"{', HALTED' if gov.get('halted') else ''}; {gov.get('open', 0)} of {gov.get('max_open')} "
                         "tickets open.")
        yield from self._stream_text("\n".join(lines))
        if top:
            yield {"type": "suggestions", "items": [f"Analyse {top[0]['sym']}", f"Chart {top[0]['sym']}",
                                                    f"Route {top[0]['sym']}"]}

    def _desk_route(self, sym):
        yield from self._run("preview_signal_routing", {"symbol": sym})
        ok, r = self._last
        if not ok or not r.get("ok", True):
            yield from self._stream_text(f"Routing preview failed: {(r or {}).get('error')}.")
            return
        accts = r.get("accounts", [])
        yield {"type": "card", "card": {"kind": "route", "symbol": sym, "accounts": [
            {"account": a.get("account"), "type": a.get("type"), "decision": a.get("decision"), "lots": a.get("lots"),
             "risk": a.get("money_at_risk"), "reasons": a.get("reasons") or []} for a in accts]}}
        counts = {}
        for a in accts:
            counts[a.get("decision")] = counts.get(a.get("decision"), 0) + 1
        sig = r.get("signal") or {}
        text = [f"Routing preview for **{sym}** {sig.get('direction') or ''} across **{len(accts)} accounts**: "
                + ", ".join(f"{v} {k}" for k, v in counts.items()) + "."]
        blocked = [a for a in accts if a.get("decision") == "BLOCKED"]
        if blocked:
            text.append("\n**Why accounts are blocked:**")
            seen = set()
            for a in blocked:
                for why in a.get("reasons") or []:
                    if why not in seen:
                        seen.add(why)
                        text.append(f"- {why}")
        if r.get("signal_problems"):
            text.append("\n**Signal problems:** " + "; ".join(map(str, r["signal_problems"])) + ".")
        text.append("\nPreview only: nothing was logged and no order was placed.")
        yield from self._stream_text("\n".join(text))

    def _desk_brief(self, sym):
        yield from self._run("get_desk_brief", {"symbol": sym})
        ok, r = self._last
        yield from self._stream_text(r.get("brief") if ok else f"No brief: {r.get('error')}.")

    def _desk_portfolio(self):
        yield from self._run("get_portfolio_summary", {})
        ok, r = self._last
        if not ok:
            yield from self._stream_text(f"Portfolio summary failed: {r.get('error')}.")
            return
        yield {"type": "card", "card": {"kind": "json", "title": "Portfolio summary", "data": r}}
        trades = r.get("trades") or {}
        lines = ["Portfolio summary from the Trading OS tracker:"]
        for k, v in trades.items():
            m = (v or {}).get("metrics") or {}
            if m:
                lines.append(f"- **{k.title()}**: {m.get('n', m.get('trades', 0))} trades, total "
                             f"{_fmt(m.get('total_r'))}R, win rate {_fmt(m.get('win_rate'))}")
        yield from self._stream_text("\n".join(lines))

    def _desk_journal(self):
        yield from self._run("get_journal", {"limit": 50})
        ok, r = self._last
        if not ok:
            yield from self._stream_text(f"I could not read your journal: {r.get('error')}.")
            return
        st, rows = r.get("stats") or {}, r.get("trades") or []
        if not rows:
            yield from self._stream_text("Your journal is empty so far. Connect a broker from **Account** and your "
                                         "closed trades will arrive by themselves.")
            return
        flags = st.get("flags") or {}
        yield {"type": "card", "card": {"kind": "journal", "title": "Your journal", "stats": {
            "trades": st.get("trades"), "win_rate": st.get("win_rate"), "avg_r": st.get("avg_r"),
            "total_r": st.get("total_r"), "to_review": st.get("to_review"), "needs_stop": st.get("needs_stop"),
            "breaches": st.get("discipline_breaches"), "with_desk": st.get("with_desk"),
            "against_desk": st.get("against_desk")}, "flags": flags}}
        out = []
        if st.get("trades"):
            out.append(f"Across **{st['trades']} journalled trades** you won **{st.get('win_rate')}%** with an "
                       f"average of **{st.get('avg_r'):+}R** ({st.get('total_r'):+}R in total).")
        else:
            out.append("None of your trades has a confirmed stop yet, so there are no R figures to judge.")
        if st.get("with_desk") or st.get("against_desk"):
            out.append(f"You traded **with** the desk {st.get('with_desk', 0)} times and **against** it "
                       f"{st.get('against_desk', 0)} times.")
        if flags:
            top = sorted(flags.items(), key=lambda kv: -kv[1])[:3]
            out.append("Most common flags: " + ", ".join(f"**{k}** ({v})" for k, v in top) + ".")
        todo = []
        if st.get("needs_stop"):
            todo.append(f"add the stop to {st['needs_stop']} trade(s)")
        if st.get("to_review"):
            todo.append(f"review {st['to_review']} trade(s)")
        if todo:
            out.append("Next: " + " and ".join(todo) + " in the **Journal**, so the figures count.")
        out.append("\n_From your own journal; nothing here is advice._")
        yield from self._stream_text("\n\n".join(out))

    @staticmethod
    def _verdict(sym, s, d):
        """The headline card for an analysis: bias, grade, action and the plan, straight from the desk."""
        lv = (d or {}).get("levels") or {}
        da = s.get("desk_action") or {}
        entry, sl = lv.get("entry"), lv.get("sl")
        tps = [lv.get(k) for k in ("tp1", "tp2") if isinstance(lv.get(k), (int, float))]
        rr = None
        if isinstance(entry, (int, float)) and isinstance(sl, (int, float)) and tps and entry != sl:
            rr = round(abs(tps[-1] - entry) / abs(entry - sl), 1)
        reg = s.get("regime") if isinstance(s.get("regime"), dict) else {}
        ses = s.get("session") if isinstance(s.get("session"), dict) else {}
        return {"type": "verdict", "symbol": sym, "name": s.get("sym_name") or sym, "price": s.get("price"),
                "dp": s.get("dp"), "change_pct": s.get("change_pct"), "direction": s.get("direction") or "NEUTRAL",
                "grade": s.get("grade"), "confidence": s.get("confidence"), "quality": s.get("signal_quality"),
                "action": da.get("mode"), "action_title": da.get("title"), "tradeable": bool(s.get("tradeable")),
                "entry": entry, "stop": sl, "targets": tps, "rr": rr, "model": lv.get("model"),
                "regime": reg.get("state"), "session": ses.get("name"), "time": s.get("ts")}

    def _desk_analyse(self, q, sym, chart_only=False, key=None):
        yield from self._run("get_market_snapshot", {"symbol": sym})
        ok, snap = self._last
        if not ok:
            yield from self._stream_text(f"No live data for **{sym}** yet: {snap.get('error')}")
            return
        yield from self._run("get_signal_detail", {"symbol": sym})   # levels for the chart
        ok_d, detail = self._last
        detail = detail if ok_d else {}
        if not chart_only:
            yield self._verdict(sym, snap, detail)
        yield from self._run("get_candles", {"symbol": sym, "limit": 96})
        ok_c, candles = self._last
        candles = candles if ok_c else {}
        chart = self._candle_chart(sym, candles, detail, snap)
        if chart:
            yield {"type": "chart", "chart": chart}
        if chart_only:
            yield from self._stream_text(self._compose(sym, snap, detail, None, True))
            yield {"type": "suggestions", "items": self._suggest(sym, snap)}
            return
        copilot = None
        filler = {"analyse", "analyze", "analysis", "snapshot", "snap", "price", "levels", "detail", "for", "me",
                  "please", "the", "a", "on", "of", "now", "today", "view", "take", "your", "what", "is", "about",
                  "how", "looking", "look", "at", "give", "can", "you", "do", "an", "quick", "full", sym.lower()}
        name_words = set(re.findall(r"[a-z]+", str(snap.get("sym_name") or "").lower()))
        word = str(snap.get("word") or "").lower()
        rest = [w for w in re.findall(r"[a-z0-9]+", (q or "").lower())
                if w not in filler and w not in name_words and w != word]
        generic = not rest
        if q and not generic:
            yield from self._run("ask_desk", {"symbol": sym, "question": q})
            ok_a, a = self._last
            copilot = a.get("answer") if ok_a else None
        # the desk team: specialists in parallel, then the lead writes the answer
        yield {"type": "team", "agents": team.TEAM}
        for ag in team.TEAM:
            yield {"type": "agent", "id": ag["id"], "status": "running"}
        reports = {}
        once = (lambda sys_p, user: self._anthropic_once(key, sys_p, user)) if key else None
        for ag, rep in team.run_team(sym, snap, detail, candles, claude=once):
            reports[ag["id"]] = rep
            yield {"type": "agent", "id": ag["id"], "status": "done", **rep}
        lead_text = None
        if key:
            try:
                lead_text = ""
                user = json.dumps({"question": q or f"Analyse {sym}", "instrument": sym, "reports": reports,
                                   "snapshot": snap, "levels": detail.get("levels"),
                                   "invalidate": (detail.get("reasons") or {}).get("invalidate"),
                                   "desk_copilot": copilot}, default=str)
                for ev in self._anthropic_stream(key, self._model(), [{"role": "user", "content": user}], None,
                                                 system=team.LEAD_PROMPT, max_tokens=900):
                    if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                        lead_text += ev["delta"]["text"]
                        yield {"type": "text", "delta": _clean(ev["delta"]["text"])}
                    elif ev.get("type") == "error":
                        raise RuntimeError("lead stream error")
            except Exception:
                if lead_text:
                    yield {"type": "text", "delta": "\n\n(The lead analyst was cut off.)"}
                else:
                    lead_text = None
        if key and (not lead_text or any(r.get("source") == "rules" for r in reports.values())):
            yield {"type": "notice", "message": "Claude was unavailable for part of this answer, so the desk's "
                                                "rule-based specialists filled in."}
        if not lead_text:
            yield from self._stream_text(team.lead_report(sym, snap, detail, reports, q, copilot, compact=True))
        yield {"type": "suggestions", "items": self._suggest(sym, snap)}

    def _compose(self, sym, s, d, answer, chart_only):
        dp = s.get("dp") if isinstance(s.get("dp"), int) else None
        da = s.get("desk_action") or {}
        name = s.get("sym_name") or sym
        chg = s.get("change_pct")
        out = [f"**{name} ({sym}) {_fmt(s.get('price'), dp)}**"
               + (f", {'up' if chg > 0 else 'down'} {abs(chg):.2f}% today" if isinstance(chg, (int, float)) and chg
                  else "") + f". Data as of {s.get('ts')}."]
        if da.get("mode") == "CLOSED":
            out.append(f"\n**Market closed.** {da.get('detail', '')}")
        out.append(f"\nBias **{s.get('direction')}** at **{s.get('confidence')}%** confidence, grade "
                   f"**{s.get('grade') or '-'}**, quality {s.get('signal_quality')}/100, timeframes "
                   f"{s.get('mtf_agreement')}% agreed. Tradeable now: **{'yes' if s.get('tradeable') else 'no'}**.")
        if da and da.get("mode") != "CLOSED":
            out.append(f"\nDesk action: **{da.get('mode')}**, {da.get('title', '')}. {da.get('detail', '')}")
        if answer:
            out.append(f"\n{answer}")
        if chart_only:
            out.append("\nThe chart shows the latest fifteen-minute bars with the desk levels where it has them.")
            return "\n".join(out)
        lv = d.get("levels") or {}
        if lv.get("entry") is not None:
            out.append("\n**Levels**")
            out.append(f"- Entry {_fmt(lv.get('entry'), dp)} ({lv.get('model', 'model n/a').lower()}), stop "
                       f"{_fmt(lv.get('sl'), dp)}, {_fmt(d.get('sl_dist'), dp)} away")
            tps = [lv.get(k) for k in ("tp1", "tp2", "tp3") if lv.get(k) is not None]
            if tps:
                out.append("- Targets " + ", ".join(_fmt(t, dp) for t in tps))
        rs = d.get("reasons") or {}
        items = rs.get("items") if isinstance(rs, dict) else None
        if items:
            out.append("\n**Evidence**")
            for i in items[:7]:
                out.append(f"- {'✓' if i.get('ok') else '✗'} {i.get('t')}")
        inval = rs.get("invalidate") if isinstance(rs, dict) else None
        if inval:
            out.append("\n**What would change the view**")
            out.extend(f"- {x}" for x in inval[:4])
        macro = d.get("macro") or {}
        rows = sorted([r for r in macro.get("rows") or [] if r.get("impact")],
                      key=lambda r: -(r.get("strength") or 0))[:2]
        if rows:
            out.append(f"\n**Macro** score {_fmt(macro.get('score'), 2)}: " + " ".join(
                f"{r.get('name')} {r.get('chg'):+}{'bp' if r.get('unit') == 'bps' else '%'}, {r.get('note', '')}"
                for r in rows))
        warns = [w for w in (d.get("warnings") or []) if "closed" not in w.lower() or da.get("mode") != "CLOSED"]
        if warns:
            out.append("\n**Warnings:** " + "; ".join(warns) + ".")
        out.append("\n_Research from the desk engine, not personalised advice._")
        return "\n".join(out)

    def _suggest(self, sym, s):
        items = [f"Route {sym}", f"Brief {sym}", "Overview"]
        if not s.get("tradeable"):
            items.insert(0, f"Why no trade on {sym}?")
        return items[:4]

    # ------------------------------------------------------------ claude mode
    def _claude(self, question, sym, history, key):
        tools = [{"name": t["name"], "description": t["description"], "input_schema": t["inputSchema"]}
                 for t in (spec.spec() for spec in self.connect.tools.values())]
        msgs = []
        for h in history[-10:]:
            if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str) and h["content"].strip():
                msgs.append({"role": h["role"], "content": h["content"][:4000]})
        msgs.append({"role": "user", "content": f"[Instrument on screen: {sym}]\n{question}"})
        model = self._model()
        charted = set()
        for _ in range(MAX_TURNS):
            blocks, stop = [], None
            for ev in self._anthropic_stream(key, model, msgs, tools):
                kind = ev.get("type")
                if kind == "content_block_start":
                    b = dict(ev["content_block"])
                    if b.get("type") == "tool_use":
                        b["_json"] = ""
                    blocks.append(b)
                elif kind == "content_block_delta":
                    d = ev["delta"]
                    if d.get("type") == "text_delta":
                        blocks[-1]["text"] = blocks[-1].get("text", "") + d["text"]
                        yield {"type": "text", "delta": _clean(d["text"])}
                    elif d.get("type") == "input_json_delta":
                        blocks[-1]["_json"] += d.get("partial_json", "")
                elif kind == "message_delta":
                    stop = (ev.get("delta") or {}).get("stop_reason")
                elif kind == "error":
                    raise RuntimeError((ev.get("error") or {}).get("message", "stream error"))
            content = []
            for b in blocks:
                if b.get("type") == "text":
                    content.append({"type": "text", "text": b.get("text", "")})
                elif b.get("type") == "tool_use":
                    try:
                        b["input"] = json.loads(b.pop("_json") or "{}")
                    except ValueError:
                        b["input"] = {}
                    content.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b["input"]})
            msgs.append({"role": "assistant", "content": content})
            if stop != "tool_use":
                return
            results = []
            for b in content:
                if b["type"] != "tool_use":
                    continue
                yield from self._run(b["name"], b["input"])
                ok, res = self._last
                results.append({"type": "tool_result", "tool_use_id": b["id"], "is_error": not ok,
                                "content": json.dumps(res, default=str)[:20000]})
                s = (b["input"] or {}).get("symbol", "").upper()
                if ok and b["name"] == "get_candles" and s not in charted:
                    charted.add(s)
                    chart = self._candle_chart(s, res)
                    if chart:
                        yield {"type": "chart", "chart": chart}
                elif ok and b["name"] == "get_market_overview" and "overview" not in charted:
                    charted.add("overview")
                    rows = [r for r in res.get("symbols", []) if r.get("status") == "ok"]
                    yield {"type": "chart", "chart": {"kind": "bars", "title": "Directional conviction by instrument",
                                                      "min": -100, "max": 100, "bars": sorted([
                                                          {"label": r["sym"], "note": r.get("direction") or "-",
                                                           "value": (r.get("confidence") or 0) * (
                                                               1 if r.get("direction") == "LONG" else
                                                               -1 if r.get("direction") == "SHORT" else 0)}
                                                          for r in rows], key=lambda x: -abs(x["value"]))}}
                elif ok and b["name"] == "preview_signal_routing":
                    yield {"type": "card", "card": {"kind": "route", "symbol": s, "accounts": [
                        {"account": a.get("account"), "type": a.get("type"), "decision": a.get("decision"),
                         "lots": a.get("lots"), "risk": a.get("money_at_risk"), "reasons": a.get("reasons") or []}
                        for a in res.get("accounts", [])]}}
            msgs.append({"role": "user", "content": results})
        yield {"type": "text", "delta": "\n\n(Stopped after the maximum number of tool rounds.)"}

    @staticmethod
    def _model():
        return os.environ.get("NORTHCHART_AGENT_MODEL", "claude-sonnet-5-5")

    def _anthropic_once(self, key, system, user, max_tokens=400):
        """One non-streamed reply (used by the specialists)."""
        url = os.environ.get("NORTHCHART_ANTHROPIC_URL", "https://api.anthropic.com/v1/messages")
        body = json.dumps({"model": os.environ.get("NORTHCHART_TEAM_MODEL", self._model()),
                           "max_tokens": max_tokens, "system": system,
                           "messages": [{"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            out = json.loads(resp.read())
        return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")

    def _anthropic_stream(self, key, model, msgs, tools, system=None, max_tokens=1500):
        req_body = {"model": model, "max_tokens": max_tokens, "system": system or SYSTEM_PROMPT,
                    "messages": msgs, "stream": True}
        if tools:
            req_body["tools"] = tools
        body = json.dumps(req_body).encode()
        url = os.environ.get("NORTHCHART_ANTHROPIC_URL", "https://api.anthropic.com/v1/messages")
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
            "accept": "text/event-stream"})
        try:
            resp = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Anthropic API {e.code}: {e.read()[:300].decode('utf-8', 'replace')}") from None
        with resp:
            data = []
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and data:
                    payload = "\n".join(data)
                    data = []
                    try:
                        yield json.loads(payload)
                    except ValueError:
                        continue
