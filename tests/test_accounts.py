"""Accounts: owner setup, sign in, sign up by invite, per-user journals, CSRF, sync tokens, MCP sign-in.

Stdlib only. Run: python -m unittest discover -s tests
"""
import base64
import hashlib
import http.client
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_agent import AUTH, DeskServer, sd, seed  # noqa: E402

import northchart_accounts as na  # noqa: E402

ORIGIN = "http://127.0.0.1"


class Client:
    """A tiny browser: keeps the session cookie and sends Origin on writes."""

    def __init__(self, port):
        self.port, self.cookie = port, None

    def req(self, method, path, body=None, headers=None, origin=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if origin:
            h["Origin"] = f"{ORIGIN}:{self.port}"
        if self.cookie:
            h["Cookie"] = "nc_s=" + self.cookie
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        raw = r.read()
        sc = r.getheader("Set-Cookie") or ""
        if sc.startswith("nc_s="):
            self.cookie = sc.split(";")[0][5:] or None
        c.close()
        try:
            return r.status, json.loads(raw or b"{}"), r
        except ValueError:
            return r.status, raw, r


class Accounts(unittest.TestCase):
    def setUp(self):
        seed()
        self.dir = tempfile.mkdtemp(prefix="nc-acc-")
        self._saved = sd._accounts
        sd._accounts = na.Accounts(self.dir, lambda: "trader", lambda: "desk-pass-123")
        sd._user_journals.clear()

    def tearDown(self):
        sd._accounts = self._saved
        sd._user_journals.clear()
        sd.enter_user(None)

    def setup_owner(self, s):
        b = Client(s.port)
        st, out, _ = b.req("POST", "/api/auth/setup", {"email": "david@example.com", "name": "David Givondo",
                                                         "password": "a-long-owner-pass", "legacy_password": "nope"})
        self.assertEqual(st, 400)
        self.assertIn("desk password", out["error"])
        st, out, r = b.req("POST", "/api/auth/setup", {"email": "david@example.com", "name": "David Givondo",
                                                         "password": "a-long-owner-pass",
                                                         "legacy_password": "desk-pass-123"})
        self.assertEqual(st, 200, out)
        self.assertIn("HttpOnly", r.getheader("Set-Cookie"))
        self.assertIn("SameSite=Lax", r.getheader("Set-Cookie"))
        return b

    def test_password_storage(self):
        h = na.hash_password("correct horse battery")
        self.assertTrue(h.startswith(("scrypt$", "pbkdf2$")))
        self.assertNotIn("correct", h)
        self.assertTrue(na.verify_password("correct horse battery", h))
        self.assertFalse(na.verify_password("wrong", h))
        self.assertIsNotNone(na.password_problem("short"))
        self.assertIsNotNone(na.password_problem("password123"))

    def test_owner_setup_signin_and_signout(self):
        with DeskServer() as s:
            b = self.setup_owner(s)
            st, me, _ = b.req("GET", "/api/auth/me")
            self.assertEqual(me["user"]["role"], "owner")
            st, _, r = b.req("GET", "/account", headers={"Accept": "text/html"})
            self.assertEqual(st, 200)
            # a second owner cannot be created
            st, out, _ = Client(s.port).req("POST", "/api/auth/setup", {"email": "x@example.com", "name": "X",
                                                                          "password": "another-long-pass",
                                                                          "legacy_password": "desk-pass-123"})
            self.assertEqual(st, 400)
            # signed-out browsers are sent to the sign-in page
            st, _, r = Client(s.port).req("GET", "/journal", headers={"Accept": "text/html"})
            self.assertEqual(st, 302)
            self.assertTrue(r.getheader("Location").startswith("/signin?next=/journal"))
            st, out, _ = b.req("POST", "/api/auth/signout", {})
            self.assertEqual(st, 200)
            self.assertIsNone(b.cookie)
            c = Client(s.port)
            st, out, _ = c.req("POST", "/api/auth/signin", {"email": "david@example.com", "password": "wrong-pass-123"})
            self.assertEqual(st, 400)
            st, out, _ = c.req("POST", "/api/auth/signin", {"email": "DAVID@example.com", "password": "a-long-owner-pass"})
            self.assertEqual(st, 200, out)
            st, out, _ = c.req("GET", "/api/journal/sheet")
            self.assertEqual(st, 200)
            # the old desk password still works for scripts this release, as the owner
            st, out, _ = Client(s.port).req("GET", "/api/journal/sheet", headers={"Authorization": AUTH})
            self.assertEqual(st, 200)

    def test_lockout_after_five_failures(self):
        with DeskServer() as s:
            self.setup_owner(s)
            c = Client(s.port)
            for _ in range(5):
                c.req("POST", "/api/auth/signin", {"email": "david@example.com", "password": "bad-password-1"})
            st, out, _ = c.req("POST", "/api/auth/signin", {"email": "david@example.com", "password": "a-long-owner-pass"})
            self.assertEqual(st, 400)
            self.assertIn("Too many", out["error"])

    def test_cross_site_post_is_refused(self):
        with DeskServer() as s:
            b = self.setup_owner(s)
            st, out, _ = b.req("POST", "/api/journal/sync", {}, origin=False)
            self.assertEqual(st, 403)
            st, out, _ = b.req("POST", "/api/journal/sync", {}, headers={"Origin": "https://evil.example"})
            self.assertEqual(st, 403)
            st, out, _ = b.req("POST", "/api/journal/sync", {})
            self.assertEqual(st, 200)

    def test_invite_signup_and_separate_journals(self):
        with DeskServer() as s:
            owner = self.setup_owner(s)
            st, out, _ = Client(s.port).req("POST", "/api/auth/signup", {"email": "amon@example.com", "name": "Amon",
                                                                           "password": "member-pass-long", "invite": "NOPE"})
            self.assertEqual(st, 400)
            st, out, _ = owner.req("POST", "/api/auth/invites", {"email": "amon@example.com"})
            code = out["code"]
            m = Client(s.port)
            st, out, _ = m.req("POST", "/api/auth/signup", {"email": "amon@example.com", "name": "Amon",
                                                             "password": "member-pass-long", "invite": code})
            self.assertEqual(st, 200, out)
            self.assertEqual(out["user"]["role"], "member")
            # the confirmation email went to the outbox because SMTP is not set
            self.assertIn("amon@example.com", open(os.path.join(self.dir, "accounts", "outbox.log")).read())
            # invite cannot be reused
            st, out, _ = Client(s.port).req("POST", "/api/auth/signup", {"email": "b@example.com", "name": "B",
                                                                           "password": "member-pass-long", "invite": code})
            self.assertEqual(st, 400)
            # the member's trade lands in the member's journal only
            tid = "M" + os.urandom(3).hex()
            st, out, _ = m.req("POST", "/api/tracker/import/fills", {
                "source": "tradingview", "broker": "Paper", "account_label": "AMON-DEMO", "account_type": "DEMO",
                "fills": [{"broker_trade_id": tid, "instrument": "XAUUSD", "direction": "BUY", "volume": 1,
                           "entry_price": 2650, "exit_price": 2655, "net_pl": 50,
                           "open_time_utc": "2026-11-20T10:00:00Z", "close_time_utc": "2026-11-20T11:00:00Z"}]})
            self.assertEqual(st, 200, out)
            mine = m.req("GET", "/api/journal/sheet")[1]["rows"]
            theirs = owner.req("GET", "/api/journal/sheet")[1]["rows"]
            self.assertTrue(any(r["id"].endswith(tid.upper()) for r in mine))
            self.assertFalse(any(r["id"].endswith(tid.upper()) for r in theirs))
            self.assertTrue(os.path.isdir(os.path.join(self.dir, "users")))
            # owner-only routes and invites are closed to members
            self.assertEqual(m.req("GET", "/api/router/accounts")[0], 403)
            self.assertEqual(m.req("POST", "/api/auth/invites", {})[0], 403)
            # members can export and delete their own data
            st, raw, r = m.req("GET", "/api/auth/export.zip")
            self.assertEqual(st, 200)
            self.assertTrue(raw.startswith(b"PK"))
            st, out, _ = m.req("POST", "/api/auth/delete", {"password": "member-pass-long"})
            self.assertEqual(st, 200, out)
            self.assertEqual(Client(s.port).req("POST", "/api/auth/signin", {"email": "amon@example.com",
                                                                               "password": "member-pass-long"})[0], 400)

    def test_password_reset_signs_out_everywhere(self):
        with DeskServer() as s:
            owner = self.setup_owner(s)
            st, out, _ = Client(s.port).req("POST", "/api/auth/forgot", {"email": "david@example.com"})
            self.assertEqual(st, 200)
            st, out, _ = Client(s.port).req("POST", "/api/auth/forgot", {"email": "nobody@example.com"})
            self.assertEqual(st, 200)                          # same answer: does not reveal who has an account
            log = open(os.path.join(self.dir, "accounts", "outbox.log")).read()
            token = log.split("/reset?token=")[-1].split()[0]
            c = Client(s.port)
            st, out, _ = c.req("POST", "/api/auth/reset", {"token": token, "password": "brand-new-pass-1"})
            self.assertEqual(st, 200, out)
            self.assertEqual(owner.req("GET", "/api/journal/sheet")[0], 401)   # old session is gone
            st, out, _ = c.req("POST", "/api/auth/reset", {"token": token, "password": "brand-new-pass-2"})
            self.assertEqual(st, 400)                           # one use only

    def test_sync_token_can_only_send_trades(self):
        with DeskServer() as s:
            owner = self.setup_owner(s)
            tok = owner.req("POST", "/api/auth/tokens", {"label": "laptop sync"})[1]["token"]
            self.assertTrue(tok.startswith("nci_"))
            h = {"Authorization": "Bearer " + tok}
            st, out, _ = Client(s.port).req("POST", "/api/tracker/import/fills", {
                "source": "tradingview", "broker": "Paper", "account_label": "TV", "account_type": "DEMO",
                "fills": [{"broker_trade_id": "S" + os.urandom(3).hex(), "instrument": "XAUUSD",
                           "direction": "SELL", "volume": 1, "entry_price": 2650, "exit_price": 2645, "net_pl": 50}]},
                headers=h, origin=False)
            self.assertEqual(st, 200, out)
            self.assertEqual(Client(s.port).req("GET", "/api/journal/sheet", headers=h, origin=False)[0], 403)
            toks = owner.req("GET", "/api/auth/tokens")[1]["tokens"]
            owner.req("POST", "/api/auth/tokens/revoke", {"id": toks[0]["id"]})
            self.assertEqual(Client(s.port).req("POST", "/api/tracker/import/fills", {"fills": []}, headers=h,
                                                origin=False)[0], 401)

    def test_mcp_consent_signs_in_with_the_account(self):
        import base64 as b64
        with DeskServer() as s:
            self.setup_owner(s)
            c = Client(s.port)
            st, reg, _ = c.req("POST", "/oauth/register", {"client_name": "Claude",
                                                           "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]},
                               origin=False)
            self.assertIn(st, (200, 201), reg)
            verifier = "v" * 50
            chal = b64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            q = {"client_id": reg["client_id"], "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                 "response_type": "code", "code_challenge": chal, "code_challenge_method": "S256", "state": "xyz"}
            conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=10)
            conn.request("GET", "/oauth/authorize?" + urllib.parse.urlencode(q))
            page = conn.getresponse().read().decode()
            self.assertIn('name="email"', page)
            form = urllib.parse.urlencode({**q, "email": "david@example.com", "password": "a-long-owner-pass"})
            conn.request("POST", "/oauth/authorize", body=form,
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            r = conn.getresponse()
            r.read()
            self.assertEqual(r.status, 302)
            code = urllib.parse.parse_qs(urllib.parse.urlsplit(r.getheader("Location")).query)["code"][0]
            conn.request("POST", "/oauth/token", body=urllib.parse.urlencode({
                "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                "client_id": reg["client_id"], "redirect_uri": "https://claude.ai/api/mcp/auth_callback"}),
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            tok = json.loads(conn.getresponse().read())["access_token"]
            conn.request("POST", "/mcp", body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                          "params": {"name": "get_journal", "arguments": {}}}),
                         headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json",
                                  "Accept": "application/json, text/event-stream"})
            out = json.loads(conn.getresponse().read())
            conn.close()
            self.assertFalse(out["result"].get("isError"), out)


if __name__ == "__main__":
    unittest.main()
