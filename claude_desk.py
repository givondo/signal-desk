"""
Claude analyst layer for Signal Desk (optional).

Turns on when the `anthropic` package is installed and credentials resolve
(ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile).
Otherwise `available()` is False and the desk keeps its rule-based copilot
and brief, unchanged.

Guardrails:
  - The system prompt is docs/PRECIFARM_DESK_ANALYST.md, frozen and cached.
  - Claude sees only a snapshot of live engine state plus the rule engine's
    own brief. It may explain, but it may not raise the desk status or
    publish a signal the rule engine did not.

Env:
  CLAUDE_DESK_MODEL    default claude-opus-5-5
  CLAUDE_DESK_EFFORT   low | medium | high | xhigh | max  (default medium)
  CLAUDE_DESK          set to "off" to force the rule-based engine
"""
from __future__ import annotations

import json
import os
import threading
import time

try:
    import anthropic
except ImportError:          # stdlib-only deploys keep working
    anthropic = None

MODEL = os.environ.get("CLAUDE_DESK_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("CLAUDE_DESK_EFFORT", "medium")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROTOCOL_FILE = os.path.join(BASE_DIR, "docs", "PRECIFARM_DESK_ANALYST.md")

# Keys from the live signal worth sending; history and raw candles are bulky
# and add nothing the engine has not already summarised.
SNAPSHOT_KEYS = (
    "sym", "sym_name", "ts", "epoch", "price", "change_pct", "day_high",
    "day_low", "dp", "direction", "confidence", "score", "grade", "aligned",
    "tradeable", "mtf_agreement", "regime", "session", "macro", "macro_score",
    "matrix", "scores", "engine", "levels", "models", "sl_dist", "pivots",
    "reasons", "desk_action", "quality", "news", "perf",
)

ASK_RULES = """
## DESK COPILOT MODE

You are answering a trader's question in the dashboard copilot panel.
- Answer only from the LIVE_STATE JSON and RULE_ENGINE_ANSWER in the message. If the answer needs data that is not there (calendar, broker spread, account), say which input is missing.
- The rule engine's desk_action, tradeable flag and DESK STATUS are binding. Never suggest entering a trade the engine has not cleared; you may explain what would need to change.
- Keep it under 120 words, plain text, no markdown headings. Lead with the answer.
"""

BRIEF_RULES = """
## BRIEF MODE

Write the desk brief in the OUTPUT FORMAT sections, numbered 1 to 7, plain text.
- RULE_ENGINE_BRIEF is the authoritative computation. Copy its DESK STATUS, SESSION CLOCK times, signal ID, entry, stop, targets, lot size and confluence score exactly. You must not upgrade WATCHLIST ONLY to OPEN or add a signal it did not publish.
- Your job is the reasoning a senior analyst adds: a sharper MARKET BRIEF (120 words max), the liquidity read, and clear upgrade triggers in WATCHLIST.
- Every number must appear in LIVE_STATE or RULE_ENGINE_BRIEF.
"""

_client = None
_client_lock = threading.Lock()
_last_error = None


def _protocol_text() -> str:
    try:
        with open(PROTOCOL_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ("You are the head analyst of the Precifarm Trading Signal Desk. "
                "Never invent prices, levels or events; cite only supplied inputs.")


PROTOCOL = _protocol_text()


def _has_credentials() -> bool:
    # The SDK builds a client without credentials and only fails on the first
    # call, so check the sources it resolves from: env vars, then a profile.
    if any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                                       "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID")):
        return True
    cfg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.isdir(os.path.join(cfg, "anthropic"))


def _get_client():
    global _client, _last_error
    if anthropic is None or os.environ.get("CLAUDE_DESK", "").lower() == "off":
        return None
    if not _has_credentials():
        _last_error = "no Anthropic credentials"
        return None
    with _client_lock:
        if _client is None:
            try:
                _client = anthropic.Anthropic(timeout=90.0, max_retries=2)
            except Exception as e:        # no credentials resolved
                _last_error = str(e)
                return None
        return _client


def available() -> bool:
    return _get_client() is not None


def status() -> dict:
    return {"enabled": available(), "model": MODEL, "effort": EFFORT,
            "last_error": _last_error}


def snapshot(sig: dict) -> dict:
    snap = {k: sig[k] for k in SNAPSHOT_KEYS if k in sig}
    if sig.get("epoch"):
        snap["age_seconds"] = int(time.time() - sig["epoch"])
    if isinstance(snap.get("news"), list):
        snap["news"] = snap["news"][:6]
    snap["recent_alerts"] = (sig.get("alerts") or [])[-5:]
    return snap


def _call(mode_rules: str, user_text: str) -> str | None:
    """One Messages API call. Returns text, or None so the caller falls back."""
    global _last_error
    client = _get_client()
    if client is None:
        return None
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": EFFORT},
            system=[
                {"type": "text", "text": PROTOCOL,
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": mode_rules},
            ],
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.AuthenticationError as e:
        _last_error = f"auth: {e.message}"
        return None
    except anthropic.RateLimitError:
        _last_error = "rate limited"
        return None
    except anthropic.APIStatusError as e:
        _last_error = f"api {e.status_code}: {e.message}"
        return None
    except anthropic.APIConnectionError:
        _last_error = "connection error"
        return None
    if resp.stop_reason == "refusal":
        _last_error = "refusal"
        return None
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    _last_error = None
    return text or None


def _payload(sig: dict, label: str, reference: str) -> str:
    return ("LIVE_STATE:\n" + json.dumps(snapshot(sig), default=str, separators=(",", ":"))
            + f"\n\n{label}:\n" + reference)


def ask(sig: dict, question: str, rule_answer: str) -> str | None:
    # The rule copilot answer, not the brief: building a brief has side
    # effects (signal IDs, disclaimer date) that must not run per question.
    return _call(ASK_RULES, _payload(sig, "RULE_ENGINE_ANSWER", rule_answer)
                 + "\n\nTRADER QUESTION:\n" + question.strip()[:1000])


def brief(sig: dict, rule_brief: str) -> str | None:
    return _call(BRIEF_RULES, _payload(sig, "RULE_ENGINE_BRIEF", rule_brief)
                 + "\n\nWrite the desk brief now.")
