"""Broker connections: statements, the Bridge, MetaApi (mocked) and sealed secrets. Stdlib only."""
import base64
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_accounts import Client  # noqa: E402
from test_agent import DeskServer, sd, seed  # noqa: E402

import northchart_accounts as na  # noqa: E402
import northchart_brokers as nb  # noqa: E402

MT5_REPORT = """<html><body><table>
<tr><th colspan=13>Trade History Report</th></tr>
<tr><th colspan=13>Positions</th></tr>
<tr><td>Time</td><td>Position</td><td>Symbol</td><td>Type</td><td>Volume</td><td>Price</td><td>S / L</td>
<td>T / P</td><td>Time</td><td>Price</td><td>Commission</td><td>Swap</td><td>Profit</td></tr>
<tr><td>2026.11.20 10:00:00</td><td>5501</td><td>XAUUSD</td><td>buy</td><td>0.10</td><td>2 650.00</td><td>2 640.00</td>
<td>2 670.00</td><td>2026.11.20 12:00:00</td><td>2 665.00</td><td>-0.70</td><td>0.00</td><td>150.00</td></tr>
<tr><td>2026.11.20 13:00:00</td><td>5502</td><td>EURUSD</td><td>sell</td><td>1.00</td><td>1.05000</td><td></td>
<td></td><td>2026.11.20 13:30:00</td><td>1.05100</td><td>-7.00</td><td>0.00</td><td>-100.00</td></tr>
<tr><th colspan=13>Orders</th></tr>
<tr><td>2026.11.20 10:00:00</td><td>9</td><td>XAUUSD</td><td>buy</td><td>0.10 / 0.10</td><td></td><td></td><td></td><td></td><td></td><td></td><td>filled</td><td></td></tr>
</table></body></html>"""

MT4_REPORT = """<html><body><table>
<tr><td colspan=13><b>Closed Transactions:</b></td></tr>
<tr><td>Ticket</td><td>Open Time</td><td>Type</td><td>Size</td><td>Item</td><td>Price</td><td>S / L</td><td>T / P</td>
<td>Close Time</td><td>Price</td><td>Commission</td><td>Taxes</td><td>Swap</td><td>Profit</td></tr>
<tr><td>7001</td><td>2026.11.19 09:00:00</td><td>balance</td><td colspan=10>Deposit</td><td>1000.00</td></tr>
<tr><td>7002</td><td>2026.11.19 09:05:00</td><td>sell</td><td>0.50</td><td>gbpusd</td><td>1.27000</td><td>1.27300</td>
<td>1.26400</td><td>2026.11.19 11:00:00</td><td>1.26500</td><td>-3.50</td><td>0.00</td><td>-0.40</td><td>250.00</td></tr>
<tr><td colspan=10>Closed P/L:</td><td>-3.50</td><td>0.00</td><td>-0.40</td><td>250.00</td></tr>
</table></body></html>"""

CTRADER_CSV = """Position ID;Symbol;Opening Direction;Opening Time;Closing Time;Entry Price;Closing Price;Closing Quantity;Net USD
88;XAUUSD;Buy;20/11/2026 08:00:00;20/11/2026 09:00:00;2650.5;2655.5;1;50
89;XAUUSD;Sell;20/11/2026 10:00:00;20/11/2026 10:30:00;2660;2662;1;-20
"""


class Parsers(unittest.TestCase):
    def test_mt5_html_in_utf16(self):
        kind, fills, info = nb.parse_statement("ReportHistory.html", MT5_REPORT.encode("utf-16"), offset_hours=2)
        self.assertEqual(kind, "MetaTrader 5 report")
        self.assertEqual(len(fills), 2)                       # orders table ignored
        f = fills[0]
        self.assertEqual((f["broker_trade_id"], f["direction"], f["stop_loss"]), ("5501", "BUY", 2640.0))
        self.assertEqual(f["open_time_utc"], "2026-11-20T08:00:00Z")      # server UTC+2 to UTC
        self.assertAlmostEqual(f["net_pl"], 149.3)
        self.assertEqual(fills[1]["stop_loss"], "")

    def test_mt4_html_skips_balance(self):
        kind, fills, _ = nb.parse_statement("Statement.htm", MT4_REPORT.encode(), 0)
        self.assertEqual(kind, "MetaTrader 4 statement")
        self.assertEqual(len(fills), 1)
        self.assertEqual((fills[0]["direction"], fills[0]["instrument"], fills[0]["stop_loss"]), ("SELL", "gbpusd", 1.273))
        self.assertAlmostEqual(fills[0]["net_pl"], 246.1)

    def test_csv_detects_columns_or_asks(self):
        kind, fills, info = nb.parse_statement("history.csv", CTRADER_CSV.encode(), 0)
        self.assertEqual(len(fills), 2, info)
        self.assertEqual(fills[0]["direction"], "BUY")
        self.assertEqual(fills[0]["net_pl"], 50.0)
        self.assertEqual(fills[0]["close_time_utc"], "2026-11-20T09:00:00Z")
        odd = "a,b,c,d\nXAUUSD,buy,1,2\n"
        _, fills, info = nb.parse_statement("x.csv", odd.encode(), 0)
        self.assertEqual(fills, [])
        self.assertIn("entry_price", info["missing"])
        _, fills, info = nb.parse_statement("x.csv", odd.encode(), 0,
                                            {"instrument": "a", "direction": "b", "entry_price": "c", "exit_price": "d"})
        self.assertEqual(len(fills), 1)

    def test_vault_round_trip_and_tamper(self):
        v = nb.Vault(tempfile.mkdtemp())
        sealed = v.seal({"account_id": "abc", "region": "london"})
        self.assertNotIn("abc", sealed)
        self.assertEqual(v.open(sealed)["region"], "london")
        blob = bytearray(base64.b64decode(sealed[3:]))
        blob[20] ^= 1
        with self.assertRaises(ValueError):
            v.open("v1:" + base64.b64encode(bytes(blob)).decode())


class MockMetaApi(BaseHTTPRequestHandler):
    investor = True
    created = []

    def log_message(self, *a):
        pass

    def _j(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        MockMetaApi.created.append(body)
        self._j(201, {"id": "acc-1", "state": "DEPLOYED"})

    def do_DELETE(self):
        self._j(204, {})

    def do_GET(self):
        if self.path.endswith("/account-information"):
            return self._j(200, {"platform": "mt5", "tradeAllowed": not MockMetaApi.investor,
                                 "investorMode": MockMetaApi.investor})
        return self._j(200, [
            {"id": "1", "type": "DEAL_TYPE_SELL", "entryType": "DEAL_ENTRY_IN", "symbol": "XAUUSD", "volume": 1,
             "price": 2660.0, "time": "2026-11-20T09:00:00.000Z", "positionId": "P9", "profit": 0,
             "commission": -3, "swap": 0, "stopLoss": 2670.0},
            {"id": "2", "type": "DEAL_TYPE_BUY", "entryType": "DEAL_ENTRY_OUT", "symbol": "XAUUSD", "volume": 1,
             "price": 2650.0, "time": "2026-11-20T10:00:00.000Z", "positionId": "P9", "profit": 100,
             "commission": -3, "swap": 0, "stopLoss": 2670.0},
            {"id": "3", "type": "DEAL_TYPE_BALANCE", "time": "2026-11-19T10:00:00.000Z", "profit": 1000}])


class Routes(unittest.TestCase):
    def setUp(self):
        seed()
        self.dir = tempfile.mkdtemp(prefix="nc-brk-")
        self._saved = sd._accounts
        sd._accounts = na.Accounts(self.dir, lambda: "trader", lambda: "desk-pass-123")
        sd._user_journals.clear()

    def tearDown(self):
        sd._accounts = self._saved
        sd._user_journals.clear()
        sd.enter_user(None)
        for k in ("NORTHCHART_METAAPI_TOKEN", "NORTHCHART_METAAPI_PROVISIONING_URL", "NORTHCHART_METAAPI_CLIENT_URL"):
            os.environ.pop(k, None)

    def member(self, s):
        owner = Client(s.port)
        owner.req("POST", "/api/auth/setup", {"email": "o@example.com", "name": "Owner", "password": "owner-pass-long",
                                              "legacy_password": "desk-pass-123"})
        code = owner.req("POST", "/api/auth/invites", {})[1]["code"]
        m = Client(s.port)
        st, out, _ = m.req("POST", "/api/auth/signup", {"email": "m@example.com", "name": "Member",
                                                         "password": "member-pass-long", "invite": code})
        self.assertEqual(st, 200, out)
        return owner, m

    def test_bridge_posts_land_in_the_members_journal(self):
        with DeskServer() as s:
            owner, m = self.member(s)
            tok = m.req("POST", "/api/auth/tokens", {"label": "Exness Bridge"})[1]["token"]
            body = {"platform": "mt5", "broker": "Exness", "server": "Exness-MT5Real8", "login": "123456",
                    "currency": "USD", "account_mode": "REAL", "positions": [
                        {"position": "777", "symbol": "XAUUSDm", "type": "buy", "volume": 0.1, "open_price": 2650.0,
                         "close_price": 2638.0, "open_time": "2026-11-20T08:00:00Z", "close_time": "2026-11-20T09:00:00Z",
                         "sl_initial": 2640.0, "sl_final": 2635.0, "tp": 0, "commission": -0.7, "swap": 0, "profit": -120}]}
            st, out, _ = Client(s.port).req("POST", "/api/ingest/bridge", body,
                                            headers={"Authorization": "Bearer " + tok}, origin=False)
            self.assertEqual(st, 200, out)
            self.assertEqual(out["added"], 1)
            st, out, _ = Client(s.port).req("POST", "/api/ingest/bridge", body,
                                            headers={"Authorization": "Bearer " + tok}, origin=False)
            self.assertEqual(out["duplicates"], 1)                 # resending is harmless
            brokers = m.req("GET", "/api/brokers")[1]
            self.assertEqual(brokers["connections"][0]["kind"], "bridge")
            self.assertEqual(brokers["connections"][0]["trades"], 1)
            self.assertTrue(any(b["id"] == "exness" and b["cma"] for b in brokers["brokers"]))
            self.assertEqual(owner.req("GET", "/api/brokers")[1]["connections"], [])
            row = next(r for r in m.req("GET", "/api/journal/sheet")[1]["rows"] if r["id"].endswith("777"))
            self.assertEqual(row["instrument"], "XAUUSD")           # broker suffix understood
            self.assertEqual(row["stop"], 2640.0)                   # risk measured from the stop at entry
            self.assertEqual(row["stop_by"], "Broker")
            self.assertIn("Moved stop", row["flags"])               # widened to 2635 before the close
            st, raw, r = m.req("GET", "/bridge/NorthchartBridge.mq5")
            self.assertEqual(st, 200)
            self.assertIn(b"WebRequest", raw)
            code = lambda src: b"\n".join(ln.split(b"//")[0] for ln in src.splitlines())   # code, not comments
            mq4 = m.req("GET", "/bridge/NorthchartBridge.mq4")[1]
            for bad in (b"OrderSend", b"CTrade", b"OrderModify", b"OrderClose", b"PositionClose", b"OrderDelete"):
                self.assertNotIn(bad + b"(", code(raw))             # the add-on cannot trade
                self.assertNotIn(bad + b"(", code(mq4))
                self.assertNotIn(bad + b" ", code(raw))

    def test_statement_import_and_mapping(self):
        with DeskServer() as s:
            owner, m = self.member(s)
            st, out, _ = m.req("POST", "/api/brokers/import", {
                "filename": "ReportHistory.html", "content_b64": base64.b64encode(MT5_REPORT.encode("utf-16")).decode(),
                "offset_hours": 2, "broker": "HFM", "account_type": "DEMO", "account_label": "HFM-DEMO-01"})
            self.assertEqual(st, 200, out)
            self.assertEqual((out["found"], out["added"]), (2, 2))
            st, out, _ = m.req("POST", "/api/brokers/import", {
                "filename": "x.csv", "content_b64": base64.b64encode(b"a,b,c,d\nXAUUSD,buy,1,2\n").decode()})
            self.assertEqual(st, 400)
            self.assertEqual(out["headers"], ["a", "b", "c", "d"])

    def test_metaapi_refuses_master_password_and_syncs(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), MockMetaApi)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        os.environ.update({"NORTHCHART_METAAPI_TOKEN": "test-token", "NORTHCHART_METAAPI_PROVISIONING_URL": url,
                           "NORTHCHART_METAAPI_CLIENT_URL": url + "/{region}"})
        try:
            with DeskServer() as s:
                owner, m = self.member(s)
                form = {"broker": "Exness", "platform": "mt5", "login": "123", "server": "Exness-MT5Real8",
                        "password": "secret-investor", "account_type": "REAL"}
                MockMetaApi.investor = False
                st, out, _ = m.req("POST", "/api/brokers/metaapi", form)
                self.assertEqual(st, 400)
                self.assertIn("investor", out["error"])
                MockMetaApi.investor = True
                st, out, _ = m.req("POST", "/api/brokers/metaapi", form)
                self.assertEqual(st, 200, out)
                cid = out["connection"]["id"]
                self.assertNotIn("secret", out["connection"])
                stored = open(os.path.join(self.dir, "users", next(iter(
                    u for u in sd._accounts.data["users"] if sd._accounts.data["users"][u]["role"] == "member")),
                    "brokers", "connections.json")).read()
                self.assertNotIn("secret-investor", stored)          # the password is never kept
                self.assertNotIn("acc-1", stored)                    # the MetaApi id is sealed
                st, out, _ = m.req("POST", "/api/brokers/sync", {"id": cid})
                self.assertEqual(st, 200, out)
                row = next(r for r in m.req("GET", "/api/journal/sheet")[1]["rows"] if r["id"].endswith("P9"))
                self.assertEqual((row["side"], row["entry"], row["exit"], row["stop"]), ("SELL", 2660.0, 2650.0, 2670.0))
                self.assertAlmostEqual(row["net"], 94.0)
                self.assertEqual(m.req("POST", "/api/brokers/remove", {"id": cid})[0], 200)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
