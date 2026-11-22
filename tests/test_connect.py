"""Northcharts Connect tests: OAuth 2.1 sign-in and the MCP endpoint, end to end
over a real HTTP server on localhost. Stdlib only, no network.

Run: python -m unittest discover -s tests
"""
import base64
import hashlib
import http.client
import json
import os
import secrets
import sys
import tempfile
import threading
import unittest
import urllib.parse

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="nc-test-")
os.environ["SIGNALDESK_PASS"] = "desk-pass-123"
os.environ.pop("NORTHCHART_PUBLIC_URL", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import northchart_connect as nc  # noqa: E402
import xauusd_trader as sd  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"


def fake_signal():
    return {"status": "ok", "ts": "2026-11-21 21:00:00", "sym": "XAUUSD", "sym_name": "GOLD SPOT",
            "grp": "Metals", "price": 2650.5, "change_pct": 0.4, "day_high": 2660.0, "day_low": 2631.2,
            "direction": "LONG", "confidence": 71, "grade": "A", "signal_quality": 68, "mtf_agreement": 75,
            "aligned": True, "tradeable": True, "regime": {"state": "TREND"},
            "session": {"name": "London/NY overlap", "liq": "high"},
            "spread_est": 0.3, "atr_1h": 6.1, "atr_d": 31.0,
            "levels": {"entry": 2649.0, "sl": 2640.0, "tp1": 2662.5, "tp2": 2676.0, "model": "LIMIT"},
            "reasons": {"items": [{"t": "H1 trend up", "ok": True}], "invalidate": ["H1 close below 2640"]},
            "warnings": [], "score": 0.42, "tech_score": 0.5, "macro_score": 0.2,
            "scores": [{"k": "TREND", "v": 72}, {"k": "VOLUME", "v": 55}, {"k": "STRUCTURE", "v": 61}], "desk_action": {"mode": "EXECUTE", "title": "Buy",
                                                                         "detail": "Limit at entry"},
            "macro": {"score": 0.2, "ts": "21:00", "rows": []}, "news": [{"title": "Gold firm", "ts": 1}],
            "history": [[1, 2648.0], [2, 2650.5]]}


class Server:
    def __enter__(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), sd.Handler)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()

    def req(self, method, path, body=None, headers=None, form=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        data = None
        if body is not None:
            if form:
                data = urllib.parse.urlencode(body).encode()
                h.setdefault("Content-Type", "application/x-www-form-urlencoded")
            else:
                data = json.dumps(body).encode()
                h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = r.read()
        c.close()
        return r.status, dict(r.getheaders()), out


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class ConnectEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with sd.state_lock:
            sd.latest["XAUUSD"] = fake_signal()

    def sign_in(self, s, redirect=CLAUDE_CB):
        st, _, out = s.req("POST", "/oauth/register", {"client_name": "Claude", "redirect_uris": [redirect]})
        self.assertEqual(st, 201, out)
        client_id = json.loads(out)["client_id"]
        verifier, challenge = pkce()
        q = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": "xyz",
             "code_challenge": challenge, "code_challenge_method": "S256", "scope": nc.SCOPE}
        st, _, page = s.req("GET", "/oauth/authorize?" + urllib.parse.urlencode(q))
        self.assertEqual(st, 200)
        self.assertIn(b"Connect Claude", page)
        self.assertIn(b"claude.ai", page)  # redirect host shown on the consent screen
        st, h, _ = s.req("POST", "/oauth/authorize", {**q, "password": "desk-pass-123"}, form=True)
        self.assertEqual(st, 302)
        loc = urllib.parse.urlsplit(h["Location"])
        params = urllib.parse.parse_qs(loc.query)
        self.assertEqual(params["state"], ["xyz"])
        self.assertTrue(h["Location"].startswith(redirect))
        st, _, out = s.req("POST", "/oauth/token", {"grant_type": "authorization_code", "code": params["code"][0],
                                                    "client_id": client_id, "redirect_uri": redirect,
                                                    "code_verifier": verifier}, form=True)
        self.assertEqual(st, 200, out)
        tok = json.loads(out)
        self.assertEqual(tok["token_type"], "Bearer")
        return client_id, tok

    def mcp(self, s, token, msg):
        return s.req("POST", "/mcp", msg, {"Authorization": "Bearer " + token,
                                           "Accept": "application/json, text/event-stream"})

    def test_discovery_and_401_points_at_metadata(self):
        with Server() as s:
            st, h, _ = s.req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"})
            self.assertEqual(st, 401)
            self.assertIn("resource_metadata=", h["WWW-Authenticate"])
            st, _, out = s.req("GET", "/.well-known/oauth-protected-resource/mcp")
            meta = json.loads(out)
            self.assertEqual(meta["resource"], f"http://127.0.0.1:{s.port}/mcp")
            st, _, out = s.req("GET", "/.well-known/oauth-authorization-server")
            asm = json.loads(out)
            self.assertEqual(asm["code_challenge_methods_supported"], ["S256"])
            self.assertIn("registration_endpoint", asm)

    def test_full_oauth_then_mcp_tools(self):
        with Server() as s:
            client_id, tok = self.sign_in(s)
            st, _, out = self.mcp(s, tok["access_token"], {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                           "params": {"protocolVersion": "2025-06-18",
                                                                      "capabilities": {},
                                                                      "clientInfo": {"name": "t", "version": "1"}}})
            init = json.loads(out)["result"]
            self.assertEqual(init["protocolVersion"], "2025-06-18")
            self.assertEqual(init["serverInfo"]["title"], "Northcharts Connect")
            st, _, out = self.mcp(s, tok["access_token"], {"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.assertEqual(st, 202)
            st, _, out = self.mcp(s, tok["access_token"], {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools = json.loads(out)["result"]["tools"]
            names = {t["name"] for t in tools}
            self.assertIn("get_market_snapshot", names)
            self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in tools))
            st, _, out = self.mcp(s, tok["access_token"], {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                                           "params": {"name": "get_market_snapshot",
                                                                      "arguments": {"symbol": "xau/usd"}}})
            res = json.loads(out)["result"]
            self.assertFalse(res["isError"])
            self.assertEqual(res["structuredContent"]["price"], 2650.5)
            self.assertEqual(res["structuredContent"]["desk_action"]["mode"], "EXECUTE")
            # agent activity is visible to the UI, under the client's name
            feed = sd.connect_service().activity_feed(5)
            self.assertEqual(feed[0]["client"], "Claude")
            self.assertEqual(feed[0]["tool"], "get_market_snapshot")

    def test_every_tool_runs_on_live_state(self):
        svc = sd.connect_service()
        args = {"symbol": "XAUUSD", "question": "where is the stop?", "limit": 5}
        for name, tool in svc.tools.items():
            props = tool.schema.get("properties", {})
            ok, result = svc.run_tool(name, {k: v for k, v in args.items() if k in props}, client="test")
            self.assertTrue(ok, f"{name}: {result}")

    def test_bad_symbol_is_a_tool_error_not_a_crash(self):
        with Server() as s:
            _, tok = self.sign_in(s)
            st, _, out = self.mcp(s, tok["access_token"], {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                                           "params": {"name": "get_signal_detail",
                                                                      "arguments": {"symbol": "DOGE"}}})
            res = json.loads(out)["result"]
            self.assertTrue(res["isError"])
            self.assertIn("unknown symbol", res["content"][0]["text"])

    def test_wrong_password_and_pkce_failure(self):
        with Server() as s:
            st, _, out = s.req("POST", "/oauth/register", {"client_name": "X", "redirect_uris": [CLAUDE_CB]})
            cid = json.loads(out)["client_id"]
            verifier, challenge = pkce()
            q = {"response_type": "code", "client_id": cid, "redirect_uri": CLAUDE_CB,
                 "code_challenge": challenge, "code_challenge_method": "S256"}
            st, _, page = s.req("POST", "/oauth/authorize", {**q, "password": "nope"}, form=True)
            self.assertEqual(st, 401)
            self.assertIn(b"Wrong desk password", page)
            st, h, _ = s.req("POST", "/oauth/authorize", {**q, "password": "desk-pass-123"}, form=True)
            code = urllib.parse.parse_qs(urllib.parse.urlsplit(h["Location"]).query)["code"][0]
            st, _, out = s.req("POST", "/oauth/token", {"grant_type": "authorization_code", "code": code,
                                                        "client_id": cid, "code_verifier": "wrong" * 10}, form=True)
            self.assertEqual(st, 400)
            self.assertEqual(json.loads(out)["error"], "invalid_grant")
            # the code is single-use even after a failed exchange
            st, _, out = s.req("POST", "/oauth/token", {"grant_type": "authorization_code", "code": code,
                                                        "client_id": cid, "code_verifier": verifier}, form=True)
            self.assertEqual(st, 400)

    def test_refresh_rotates(self):
        with Server() as s:
            cid, tok = self.sign_in(s)
            st, _, out = s.req("POST", "/oauth/token", {"grant_type": "refresh_token",
                                                        "refresh_token": tok["refresh_token"], "client_id": cid},
                               form=True)
            self.assertEqual(st, 200)
            new = json.loads(out)
            self.assertNotEqual(new["refresh_token"], tok["refresh_token"])
            st, _, out = s.req("POST", "/oauth/token", {"grant_type": "refresh_token",
                                                        "refresh_token": tok["refresh_token"], "client_id": cid},
                               form=True)
            self.assertEqual(json.loads(out)["error"], "invalid_grant")

    def test_redirect_allowlist_and_loopback_any_port(self):
        self.assertFalse(nc.redirect_allowed("https://evil.example/cb"))
        self.assertTrue(nc.redirect_allowed("https://chatgpt.com/connector_platform_oauth_redirect"))
        self.assertTrue(nc.redirect_matches(["http://localhost/callback"], "http://localhost:51234/callback"))
        self.assertFalse(nc.redirect_matches(["http://localhost/callback"], "http://localhost:5/other"))
        with Server() as s:
            st, _, out = s.req("POST", "/oauth/register", {"redirect_uris": ["https://evil.example/cb"]})
            self.assertEqual(st, 400)

    def test_static_token_for_header_clients(self):
        os.environ["NORTHCHART_MCP_TOKEN"] = "t" * 32
        try:
            with Server() as s:
                st, _, out = self.mcp(s, "t" * 32, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                self.assertEqual(st, 200)
        finally:
            del os.environ["NORTHCHART_MCP_TOKEN"]

    def test_basic_auth_still_guards_the_desk_and_agent_api(self):
        with Server() as s:
            st, _, _ = s.req("GET", "/api/agent/status")
            self.assertEqual(st, 401)
            auth = "Basic " + base64.b64encode(b"trader:desk-pass-123").decode()
            st, _, out = s.req("POST", "/api/agent/run", {"tool": "list_instruments"},
                               {"Authorization": auth})
            self.assertEqual(st, 200)
            self.assertTrue(json.loads(out)["ok"])
            st, _, out = s.req("GET", "/api/agent/status", headers={"Authorization": auth})
            self.assertTrue(json.loads(out)["mcp_url"].endswith("/mcp"))


if __name__ == "__main__":
    unittest.main()
