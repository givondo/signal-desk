"""Northchart Connect: the agent-facing interface of Northchart.

A remote Model Context Protocol (MCP) server that lets ChatGPT, Claude and
any other MCP client use the desk's read-only tools over Streamable HTTP,
plus the small OAuth 2.1 authorisation server those clients need to sign in.

Design rules
------------
* One backend, two channels. The tools here call the same engine state the
  web UI reads. The UI's agent panel runs these exact tools through
  ``run_tool`` so a person and an agent always see the same answer.
* Read-only. Every tool carries ``readOnlyHint: true``. Nothing here can place,
  change or cancel an order, edit the journal or change an account
  (decision D-011 stands).
* Owner sign-in. The desk has one owner. A client is approved on a consent
  page by entering the desk password (``SIGNALDESK_PASS``), or
  ``NORTHCHART_OWNER_PASS`` if set. Clients that send a fixed header (Claude
  Code, scripts) can use ``NORTHCHART_MCP_TOKEN`` as a bearer token instead.
* Tokens never travel in a URL. Access tokens are accepted only in the
  ``Authorization`` header and are stored hashed.

Standard library only, like the rest of the server.
"""

from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
import urllib.parse

SERVER_NAME = "northchart-connect"
SERVER_TITLE = "Northchart Connect"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SCOPE = "northchart.read"

ACCESS_TTL = 3600                # 1 hour
REFRESH_TTL = 30 * 24 * 3600     # 30 days, rotated on every use
CODE_TTL = 120                   # authorisation codes live 2 minutes
MAX_CLIENTS = 200
MAX_BODY = 256 * 1024

# Redirect URIs a client may register. Hosted Claude and ChatGPT use fixed
# callbacks; Claude Code and local tools use a loopback redirect on any port.
DEFAULT_REDIRECT_PREFIXES = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://chat.openai.com/connector_platform_oauth_redirect",
    "https://chatgpt.com/aip/",
    "https://platform.openai.com/",
)
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")

PUBLIC_PATH_PREFIXES = ("/mcp", "/.well-known/oauth-", "/.well-known/openid-configuration",
                        "/oauth/")


def is_public_path(path: str) -> bool:
    """Paths that handle their own auth (OAuth/bearer), not HTTP Basic."""
    return any(path == p or path.startswith(p) for p in PUBLIC_PATH_PREFIXES)


# ------------------------------------------------------------------ helpers

def _now() -> int:
    return int(time.time())


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _b64url_sha256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _json(status: int, obj, extra=None):
    headers = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    headers.update(extra or {})
    return status, headers, json.dumps(obj).encode()


def _cors(headers: dict) -> dict:
    # Bearer tokens only, no cookies, so a permissive CORS policy cannot be
    # used to ride on a browser session. Lets browser-based MCP inspectors work.
    headers = dict(headers)
    headers.setdefault("Access-Control-Allow-Origin", "*")
    headers.setdefault("Access-Control-Allow-Headers",
                       "Authorization, Content-Type, Mcp-Protocol-Version, Mcp-Session-Id, X-Api-Key")
    headers.setdefault("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
    headers.setdefault("Access-Control-Expose-Headers", "WWW-Authenticate, Mcp-Session-Id")
    return headers


def redirect_allowed(uri: str) -> bool:
    try:
        u = urllib.parse.urlsplit(uri)
    except ValueError:
        return False
    if u.fragment:
        return False
    if u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1"):
        return True
    if u.scheme != "https":
        return False
    extra = [p.strip() for p in os.environ.get("NORTHCHART_REDIRECT_ALLOW", "").split(",") if p.strip()]
    return any(uri.startswith(p) for p in (*DEFAULT_REDIRECT_PREFIXES, *extra))


def redirect_matches(registered: list, given: str) -> bool:
    """Exact match, except loopback redirects match on any port (RFC 8252 7.3)."""
    if given in registered:
        return True
    try:
        g = urllib.parse.urlsplit(given)
    except ValueError:
        return False
    if g.scheme != "http" or g.hostname not in ("localhost", "127.0.0.1", "::1"):
        return False
    for r in registered:
        try:
            u = urllib.parse.urlsplit(r)
        except ValueError:
            continue
        if (u.scheme, u.hostname, u.path or "/") == (g.scheme, g.hostname, g.path or "/"):
            return True
    return False


# ------------------------------------------------------------------ storage

class Store:
    """OAuth clients, codes and tokens, persisted as JSON (token hashes only)."""

    def __init__(self, path: str | None):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"clients": {}, "codes": {}, "access": {}, "refresh": {}}
        self._load()

    def _load(self):
        if not self.path:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            for k in self.data:
                if isinstance(d.get(k), dict):
                    self.data[k] = d[k]
        except (OSError, ValueError):
            pass

    def save(self):
        if not self.path:
            return
        self._purge()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _purge(self):
        now = _now()
        for k in ("codes", "access", "refresh"):
            self.data[k] = {h: v for h, v in self.data[k].items() if v.get("exp", 0) > now}
        clients = self.data["clients"]
        if len(clients) > MAX_CLIENTS:
            keep = sorted(clients.items(), key=lambda kv: kv[1].get("created", 0))[-MAX_CLIENTS:]
            self.data["clients"] = dict(keep)


# ------------------------------------------------------------------ tools

class Tool:
    def __init__(self, name, title, description, schema, fn, private=False):
        self.name = name
        self.title = title
        self.description = description
        self.schema = schema
        self.fn = fn
        self.private = private

    def spec(self):
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.schema,
            "annotations": {"title": self.title, "readOnlyHint": True, "destructiveHint": False,
                            "idempotentHint": True, "openWorldHint": False},
        }


def _sym_schema(extra=None, required=("symbol",)):
    props = {"symbol": {"type": "string",
                        "description": "Instrument code, for example XAUUSD, EURUSD, BTCUSD, NAS100. "
                                       "Call list_instruments for the full list."}}
    props.update(extra or {})
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


SNAPSHOT_KEYS = ("sym", "sym_name", "grp", "ts", "price", "change_pct", "day_high", "day_low",
                 "direction", "confidence", "grade", "signal_quality", "mtf_agreement", "aligned",
                 "tradeable", "regime", "session", "spread_est", "atr_1h", "atr_d")


class ToolError(Exception):
    pass


def build_tools(ctx) -> dict:
    """ctx supplies the engine: see Connect.__init__ for the expected keys."""

    def sym_of(args):
        s = str(args.get("symbol") or "").upper().replace("/", "").strip()
        if s not in ctx["symbols"]():
            raise ToolError(f"unknown symbol '{s}'. Use list_instruments.")
        return s

    def live(args):
        s = sym_of(args)
        st = ctx["latest"](s) or {}
        if st.get("status") != "ok":
            raise ToolError(f"{s} has no live data yet (status: {st.get('status', 'starting')}"
                            f"{', ' + str(st.get('error')) if st.get('error') else ''}).")
        return s, st

    def list_instruments(args):
        return {"instruments": [{"symbol": k, "name": v.get("name"), "group": v.get("grp")}
                                for k, v in ctx["symbols"]().items()]}

    def overview(args):
        return ctx["roster"]()

    def snapshot(args):
        s, st = live(args)
        out = {k: st.get(k) for k in SNAPSHOT_KEYS if k in st}
        da = st.get("desk_action") or {}
        out["desk_action"] = {k: da.get(k) for k in ("mode", "title", "detail") if k in da}
        out["data_note"] = "Prices are from the desk feed and may be delayed. Not investment advice."
        return out

    def detail(args):
        s, st = live(args)
        out = {k: st.get(k) for k in SNAPSHOT_KEYS if k in st}
        for k in ("levels", "reasons", "warnings", "scores", "matrix", "pivots", "ema", "sl_dist",
                  "sl_basis", "hold_est", "exp_dd", "desk_action", "score", "tech_score", "macro_score"):
            if k in st:
                out[k] = st[k]
        macro = st.get("macro") or {}
        out["macro"] = {"score": macro.get("score"), "ts": macro.get("ts"), "rows": macro.get("rows")}
        out["headlines"] = [{"title": n.get("title"), "ts": n.get("ts") or n.get("t")}
                            for n in (st.get("news") or [])[:8] if isinstance(n, dict)]
        out["data_note"] = "Analysis output of the Northchart engine, not personalised advice."
        return out

    def history(args):
        s, st = live(args)
        lim = args.get("limit", 120)
        try:
            lim = max(1, min(480, int(lim)))
        except (TypeError, ValueError):
            lim = 120
        pts = (st.get("history") or [])[-lim:]
        return {"symbol": s, "points": [{"t": p[0], "price": p[1]} for p in pts if len(p) >= 2],
                "interval_note": "One point per engine poll (about every 15 seconds), newest last."}

    def brief(args):
        s, _ = live(args)
        return {"symbol": s, "brief": ctx["brief"](s)}

    def ask(args):
        s, _ = live(args)
        q = str(args.get("question") or "").strip()
        if not q:
            raise ToolError("question is required")
        return {"symbol": s, "question": q, "answer": ctx["ask"](s, q[:500])}

    def route(args):
        s = sym_of(args)
        return ctx["route_preview"](s)

    def portfolio(args):
        return ctx["portfolio"]()

    tools = [
        Tool("list_instruments", "List instruments",
             "List every instrument Northchart covers, with its group (Metals, FX, Crypto, Indices, Energy).",
             {"type": "object", "properties": {}, "additionalProperties": False}, list_instruments),
        Tool("get_market_overview", "Market overview",
             "One row per instrument: price, direction, grade, confidence, tradeable flag and desk mode, "
             "plus the desk-wide risk governor (daily R, halt state).",
             {"type": "object", "properties": {}, "additionalProperties": False}, overview),
        Tool("get_market_snapshot", "Market snapshot",
             "Current state of one instrument: price, day range, direction, confidence, grade, quality, "
             "multi-timeframe agreement, regime, session and the desk's action (WAIT, PREPARE, EXECUTE...).",
             _sym_schema(), snapshot),
        Tool("get_signal_detail", "Signal detail",
             "Full analysis for one instrument: entry, stop and target levels, the reasons and warnings behind "
             "the score, per-timeframe matrix, pivots, EMAs, macro context and recent headlines.",
             _sym_schema(), detail),
        Tool("get_price_history", "Price history",
             "Recent intraday prices recorded by the engine for one instrument (one point per poll, up to 480).",
             _sym_schema({"limit": {"type": "integer", "minimum": 1, "maximum": 480,
                                    "description": "Number of most recent points, default 120."}}), history),
        Tool("get_desk_brief", "Desk brief",
             "The plain-text desk brief for one instrument: desk status, session clock, market brief, "
             "signals, watchlist and no-trade notes, following the desk's risk-first protocol.",
             _sym_schema(), brief),
        Tool("ask_desk", "Ask the desk",
             "Ask the desk's rule-based copilot a question about one instrument (for example 'why no trade?', "
             "'where is the stop?'). Answers come only from live desk state.",
             _sym_schema({"question": {"type": "string", "description": "The question, up to 500 characters."}},
                         required=("symbol", "question")), ask),
        Tool("preview_signal_routing", "Preview signal routing",
             "Show how the current signal for an instrument would be sized and gated for every account in the "
             "portfolio (TICKET, WATCH, DRY_RUN or BLOCKED with the reasons). Preview only: nothing is logged "
             "and no order is ever placed.",
             _sym_schema(), route, private=True),
        Tool("get_portfolio_summary", "Portfolio summary",
             "Owner's tracker summary: performance by account type, prop account status and trades waiting "
             "in the journal inbox.",
             {"type": "object", "properties": {}, "additionalProperties": False}, portfolio, private=True),
    ]
    return {t.name: t for t in tools}


# ------------------------------------------------------------------ server

class Connect:
    """MCP endpoint + OAuth authorisation server.

    ctx keys: symbols() -> dict, latest(sym) -> dict, roster() -> dict,
    brief(sym) -> str, ask(sym, q) -> str, route_preview(sym) -> dict,
    portfolio() -> dict, owner_password() -> str|None.
    """

    def __init__(self, ctx: dict, data_dir: str | None):
        self.ctx = ctx
        self.tools = build_tools(ctx)
        path = os.path.join(data_dir, "northchart_connect", "oauth.json") if data_dir else None
        self.store = Store(path)
        self.activity = collections.deque(maxlen=300)
        self.fail = collections.defaultdict(list)   # ip -> failed password timestamps
        self.lock = threading.Lock()

    # ---------------------------------------------------------- shared tool runner
    def run_tool(self, name: str, args: dict | None, client: str = "Northchart UI"):
        t = self.tools.get(name)
        started = time.time()
        entry = {"ts": _now(), "client": client, "tool": name,
                 "args": {k: v for k, v in (args or {}).items() if k in ("symbol", "question", "limit")}}
        try:
            if t is None:
                raise ToolError(f"unknown tool '{name}'")
            if not isinstance(args, dict):
                args = {}
            result = t.fn(args)
            entry.update(ok=True)
            return True, result
        except ToolError as e:
            entry.update(ok=False, error=str(e))
            return False, {"error": str(e)}
        except Exception as e:  # engine errors must not crash the server
            entry.update(ok=False, error=f"internal error: {type(e).__name__}")
            return False, {"error": f"internal error: {type(e).__name__}: {e}"}
        finally:
            entry["ms"] = int((time.time() - started) * 1000)
            with self.lock:
                self.activity.appendleft(entry)

    def activity_feed(self, limit=50):
        with self.lock:
            return list(self.activity)[:limit]

    def status(self, base_url: str):
        with self.store.lock:
            clients = sorted(self.store.data["clients"].values(), key=lambda c: c.get("created", 0), reverse=True)
            live = {}
            for rec in self.store.data["access"].values():
                if rec.get("exp", 0) > _now():
                    live[rec.get("client_id")] = live.get(rec.get("client_id"), 0) + 1
        seen = {}
        for a in self.activity_feed(300):
            seen.setdefault(a["client"], a["ts"])
        return {
            "mcp_url": base_url + "/mcp",
            "protocol_versions": list(PROTOCOL_VERSIONS),
            "static_token": bool(os.environ.get("NORTHCHART_MCP_TOKEN")),
            "owner_password_set": bool(self.ctx["owner_password"]()),
            "clients": [{"name": c.get("client_name") or "Unnamed client",
                         "redirect_host": urllib.parse.urlsplit((c.get("redirect_uris") or [""])[0]).hostname,
                         "created": c.get("created"), "connected": bool(live.get(c["client_id"])),
                         "last_call": seen.get(c.get("client_name") or "Unnamed client")}
                        for c in clients[:20]],
            "tools": [t.spec() for t in self.tools.values()],
        }

    def revoke_all(self):
        with self.store.lock:
            self.store.data["access"] = {}
            self.store.data["refresh"] = {}
            self.store.data["codes"] = {}
            self.store.save()

    # ---------------------------------------------------------- HTTP entry point
    def handle(self, method: str, path: str, query: str, headers, body: bytes, base_url: str, ip: str):
        """Returns (status, headers, body)."""
        if method == "OPTIONS":
            return 204, _cors({"Content-Length": "0"}), b""
        if path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            return self._cors_json(200, self.protected_resource_metadata(base_url))
        if path in ("/.well-known/oauth-authorization-server", "/.well-known/oauth-authorization-server/mcp",
                    "/.well-known/openid-configuration"):
            return self._cors_json(200, self.as_metadata(base_url))
        if path == "/oauth/register" and method == "POST":
            return self._cors_json(*self.register(body))
        if path == "/oauth/authorize":
            if method == "GET":
                return self.authorize_page(urllib.parse.parse_qs(query))
            if method == "POST":
                return self.authorize_submit(urllib.parse.parse_qs(body.decode("utf-8", "replace")), base_url, ip)
        if path == "/oauth/token" and method == "POST":
            ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype == "application/json":
                try:
                    form = {k: [str(v)] for k, v in json.loads(body or b"{}").items()}
                except ValueError:
                    form = {}
            else:
                form = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
            return self._cors_json(*self.token(form, base_url))
        if path == "/mcp":
            return self.mcp(method, headers, body, base_url)
        return 404, {"Content-Type": "text/plain"}, b"not found"

    def _cors_json(self, status, obj, extra=None):
        s, h, b = _json(status, obj, extra)
        return s, _cors(h), b

    # ---------------------------------------------------------- discovery
    def protected_resource_metadata(self, base):
        return {"resource": base + "/mcp", "authorization_servers": [base],
                "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"],
                "resource_name": SERVER_TITLE}

    def as_metadata(self, base):
        return {
            "issuer": base,
            "authorization_endpoint": base + "/oauth/authorize",
            "token_endpoint": base + "/oauth/token",
            "registration_endpoint": base + "/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [SCOPE, "offline_access"],
            "service_documentation": base + "/#agents",
        }

    # ---------------------------------------------------------- DCR (RFC 7591)
    def register(self, body: bytes):
        try:
            req = json.loads(body or b"{}")
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            return 400, {"error": "invalid_client_metadata", "error_description": "body must be a JSON object"}
        uris = req.get("redirect_uris")
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
            return 400, {"error": "invalid_redirect_uri", "error_description": "redirect_uris is required"}
        bad = [u for u in uris if not redirect_allowed(u)]
        if bad:
            return 400, {"error": "invalid_redirect_uri",
                         "error_description": "redirect URI not allowed on this server: " + bad[0]}
        auth_method = req.get("token_endpoint_auth_method") or "none"
        if auth_method != "none":
            auth_method = "none"   # public clients only; PKCE protects the code
        client = {
            "client_id": "nc_" + secrets.token_urlsafe(18),
            "client_id_issued_at": _now(),
            "client_name": str(req.get("client_name") or "Unnamed client")[:80],
            "redirect_uris": uris[:10],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": auth_method,
            "scope": SCOPE,
        }
        with self.store.lock:
            self.store.data["clients"][client["client_id"]] = {**client, "created": _now()}
            self.store.save()
        return 201, client

    # ---------------------------------------------------------- authorise
    def _validate_auth_request(self, q):
        g = lambda k: (q.get(k) or [""])[0]
        client = self.store.data["clients"].get(g("client_id"))
        if not client:
            return None, "Unknown client. Remove the connector and add it again."
        if not redirect_matches(client.get("redirect_uris", []), g("redirect_uri")):
            return None, "The redirect address does not match the one this client registered."
        if g("response_type") != "code":
            return None, "Only the authorisation code flow is supported."
        if g("code_challenge_method") != "S256" or len(g("code_challenge")) < 43:
            return None, "A PKCE S256 code challenge is required."
        return client, None

    def _page(self, status, title, inner):
        doc = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{margin:0;font:15px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif;background:#0b0d10;color:#e5e7eb;
display:grid;place-items:center;min-height:100vh}}.card{{background:#12161d;border:1px solid #222a36;border-radius:14px;
padding:28px;max-width:420px;width:calc(100% - 40px)}}h1{{font-size:20px;margin:0 0 4px}}.mut{{color:#9ca3af;font-size:13px}}
.mark{{display:inline-grid;place-items:center;width:34px;height:34px;border-radius:9px;background:#1d4ed8;color:#fff;
font-weight:800;margin-bottom:14px}}ul{{padding-left:18px;color:#cbd5e1}}input{{width:100%;box-sizing:border-box;padding:11px 12px;
border-radius:9px;border:1px solid #334155;background:#0b0d10;color:#e5e7eb;font-size:15px;margin:6px 0 14px}}
button{{width:100%;padding:11px;border:0;border-radius:9px;background:#2563eb;color:#fff;font-weight:700;font-size:15px;cursor:pointer}}
.warn{{background:#3b2a0b;border:1px solid #7c5c14;color:#fcd34d;padding:9px 11px;border-radius:9px;font-size:13px}}
.err{{background:#3b0f0f;border:1px solid #7f1d1d;color:#fca5a5;padding:9px 11px;border-radius:9px;font-size:13px;margin-bottom:12px}}
code{{background:#0b0d10;padding:1px 5px;border-radius:5px}}</style></head><body><div class="card">
<div class="mark">N</div>{inner}</div></body></html>"""
        return status, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                        "X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'"}, doc.encode()

    def authorize_page(self, q, error=None):
        with self.store.lock:
            client, err = self._validate_auth_request(q)
        if err:
            return self._page(400, "Northchart Connect", f"<h1>Cannot connect</h1><p class='mut'>{html.escape(err)}</p>")
        g = lambda k: (q.get(k) or [""])[0]
        host = urllib.parse.urlsplit(g("redirect_uri")).hostname or "?"
        loopback = host in ("localhost", "127.0.0.1", "::1")
        hidden = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(g(k))}">'
                         for k in ("client_id", "redirect_uri", "state", "code_challenge",
                                   "code_challenge_method", "scope", "resource", "response_type"))
        need_pw = bool(self.ctx["owner_password"]())
        pw = ('<label class="mut" for="pw">Desk password</label>'
              '<input id="pw" name="password" type="password" autocomplete="current-password" autofocus required>'
              if need_pw else
              '<p class="warn">No desk password is set on this server, so anyone who can reach it can approve. '
              'Set SIGNALDESK_PASS before exposing it.</p>')
        warn = ('<p class="warn">This app will receive its sign-in on your own computer '
                f'(<code>{html.escape(host)}</code>). Only approve if you started this connection.</p>'
                if loopback else "")
        inner = (f"<h1>Connect {html.escape(client['client_name'])}</h1>"
                 f"<p class='mut'>wants read-only access to your Northchart desk. "
                 f"You will be returned to <b>{html.escape(host)}</b>.</p>"
                 "<ul><li>Read market snapshots, signal detail, briefs and price history</li>"
                 "<li>Preview how a signal routes across your accounts</li>"
                 "<li>Read your portfolio summary</li></ul>"
                 "<p class='mut'>It cannot place, change or cancel orders, or edit your journal.</p>"
                 + (f"<div class='err'>{html.escape(error)}</div>" if error else "") + warn +
                 f'<form method="post" action="/oauth/authorize">{hidden}{pw}'
                 '<button type="submit">Approve read-only access</button></form>')
        return self._page(200 if not error else 401, "Connect to Northchart", inner)

    def authorize_submit(self, form, base, ip):
        g = lambda k: (form.get(k) or [""])[0]
        now = time.time()
        with self.lock:
            self.fail[ip] = [t for t in self.fail[ip] if now - t < 900]
            if len(self.fail[ip]) >= 5:
                return self._page(429, "Northchart Connect",
                                  "<h1>Too many attempts</h1><p class='mut'>Wait 15 minutes and try again.</p>")
        with self.store.lock:
            client, err = self._validate_auth_request(form)
        if err:
            return self._page(400, "Northchart Connect", f"<h1>Cannot connect</h1><p class='mut'>{html.escape(err)}</p>")
        owner = self.ctx["owner_password"]()
        if owner and not hmac.compare_digest(g("password").encode(), owner.encode()):
            with self.lock:
                self.fail[ip].append(now)
            return self.authorize_page(form, error="Wrong desk password.")
        code = secrets.token_urlsafe(32)
        with self.store.lock:
            self.store.data["codes"][_hash(code)] = {
                "client_id": client["client_id"], "redirect_uri": g("redirect_uri"),
                "challenge": g("code_challenge"), "scope": SCOPE,
                "resource": g("resource") or base + "/mcp", "exp": _now() + CODE_TTL}
            self.store.save()
        params = {"code": code, "iss": base}
        if g("state"):
            params["state"] = g("state")
        sep = "&" if "?" in g("redirect_uri") else "?"
        return 302, {"Location": g("redirect_uri") + sep + urllib.parse.urlencode(params),
                     "Cache-Control": "no-store", "Content-Length": "0"}, b""

    # ---------------------------------------------------------- token
    def _issue(self, client_id, scope, resource):
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.store.data["access"][_hash(access)] = {"client_id": client_id, "scope": scope,
                                                    "resource": resource, "exp": _now() + ACCESS_TTL}
        self.store.data["refresh"][_hash(refresh)] = {"client_id": client_id, "scope": scope,
                                                      "resource": resource, "exp": _now() + REFRESH_TTL}
        self.store.save()
        return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL,
                "refresh_token": refresh, "scope": scope}

    def token(self, form, base):
        g = lambda k: (form.get(k) or [""])[0]
        grant = g("grant_type")
        with self.store.lock:
            if grant == "authorization_code":
                rec = self.store.data["codes"].pop(_hash(g("code")), None)
                self.store.save()
                if not rec or rec["exp"] < _now():
                    return 400, {"error": "invalid_grant", "error_description": "code is invalid or expired"}
                if g("client_id") and g("client_id") != rec["client_id"]:
                    return 400, {"error": "invalid_grant", "error_description": "client mismatch"}
                if g("redirect_uri") and g("redirect_uri") != rec["redirect_uri"]:
                    return 400, {"error": "invalid_grant", "error_description": "redirect_uri mismatch"}
                if not g("code_verifier") or not hmac.compare_digest(_b64url_sha256(g("code_verifier")),
                                                                     rec["challenge"]):
                    return 400, {"error": "invalid_grant", "error_description": "PKCE verification failed"}
                return 200, self._issue(rec["client_id"], rec["scope"], rec["resource"])
            if grant == "refresh_token":
                rec = self.store.data["refresh"].pop(_hash(g("refresh_token")), None)
                if not rec or rec["exp"] < _now():
                    self.store.save()
                    return 400, {"error": "invalid_grant", "error_description": "refresh token is invalid or expired"}
                if g("client_id") and g("client_id") != rec["client_id"]:
                    self.store.save()
                    return 400, {"error": "invalid_grant", "error_description": "client mismatch"}
                if rec["client_id"] not in self.store.data["clients"]:
                    self.store.save()
                    return 400, {"error": "invalid_grant", "error_description": "client no longer registered"}
                return 200, self._issue(rec["client_id"], rec["scope"], rec["resource"])
        return 400, {"error": "unsupported_grant_type"}

    # ---------------------------------------------------------- bearer check
    def authenticate(self, headers, base):
        """Return the caller's display name, or None."""
        h = headers.get("Authorization") or ""
        tok = h[7:].strip() if h[:7].lower() == "bearer " else (headers.get("X-Api-Key") or "").strip()
        if not tok:
            return None
        static = os.environ.get("NORTHCHART_MCP_TOKEN")
        if static and len(static) >= 24 and hmac.compare_digest(tok.encode(), static.encode()):
            return "Token client"
        with self.store.lock:
            rec = self.store.data["access"].get(_hash(tok))
            if not rec or rec.get("exp", 0) < _now():
                return None
            if rec.get("resource") and rec["resource"].rstrip("/") != (base + "/mcp").rstrip("/"):
                return None   # audience check: token was issued for a different resource
            client = self.store.data["clients"].get(rec["client_id"])
        if not client:
            return None
        return client.get("client_name") or "Unnamed client"

    # ---------------------------------------------------------- MCP (Streamable HTTP)
    def mcp(self, method, headers, body, base):
        if method in ("GET", "HEAD", "DELETE"):
            # Stateless server: no server-initiated stream and no sessions.
            if not self.authenticate(headers, base):
                return self._unauthorized(base)
            return 405, _cors({"Allow": "POST, OPTIONS", "Content-Length": "0"}), b""
        if method != "POST":
            return 405, _cors({"Allow": "POST, OPTIONS"}), b""
        client = self.authenticate(headers, base)
        if not client:
            return self._unauthorized(base)
        if len(body) > MAX_BODY:
            return self._cors_json(413, self._err(None, -32600, "request too large"))
        try:
            msg = json.loads(body or b"null")
        except ValueError:
            return self._cors_json(400, self._err(None, -32700, "parse error"))
        if isinstance(msg, list):
            replies = [r for r in (self._dispatch(m, client) for m in msg) if r is not None]
            if not replies:
                return 202, _cors({"Content-Length": "0"}), b""
            return self._cors_json(200, replies)
        reply = self._dispatch(msg, client)
        if reply is None:
            return 202, _cors({"Content-Length": "0"}), b""
        return self._cors_json(200, reply)

    def _unauthorized(self, base):
        meta = base + "/.well-known/oauth-protected-resource/mcp"
        return self._cors_json(401, {"error": "invalid_token", "error_description": "sign in to Northchart"},
                               {"WWW-Authenticate": f'Bearer resource_metadata="{meta}", scope="{SCOPE}"'})

    @staticmethod
    def _err(mid, code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    def _dispatch(self, msg, client):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return self._err(None, -32600, "invalid request")
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        is_notification = "id" not in msg
        if method is None:          # a response from the client: nothing to do
            return None
        if is_notification:
            return None
        if method == "initialize":
            asked = params.get("protocolVersion")
            ver = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": ver,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": SERVER_TITLE, "version": SERVER_VERSION},
                "instructions": (
                    "Northchart is a multi-asset trading research desk (gold first, plus FX, crypto, indices and "
                    "oil). Start with get_market_overview, then get_market_snapshot or get_signal_detail for one "
                    "symbol. All tools are read-only and nothing can place an order. Always state the data "
                    "timestamp, treat output as research rather than personalised advice, and never invent "
                    "levels the tools did not return."),
            }}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [t.spec() for t in self.tools.values()]}}
        if method == "tools/call":
            name = params.get("name")
            if name not in self.tools:
                return self._err(mid, -32602, f"unknown tool: {name}")
            ok, result = self.run_tool(name, params.get("arguments") or {}, client=client)
            text = json.dumps(result, default=str)
            if isinstance(result, dict) and isinstance(result.get("brief"), str):
                text = result["brief"]
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}],
                "structuredContent": result if isinstance(result, dict) else {"result": result},
                "isError": not ok}}
        if method in ("resources/list", "prompts/list"):
            key = method.split("/")[0]
            return {"jsonrpc": "2.0", "id": mid, "result": {key: []}}
        return self._err(mid, -32601, f"method not found: {method}")
