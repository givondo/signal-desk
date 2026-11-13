"""Trading tracker tests. Stdlib only, no network.

Run: python -m unittest discover -s tests
All rows below are synthetic test inputs, not trading records.
"""
import csv
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="sd-test-"))
sys.path.insert(0, ROOT)

import trading_tracker as tt  # noqa: E402

TRADE = {"account_type": "demo", "account_label": "DEMO-1", "instrument": "XAUUSD", "direction": "buy",
         "entry_price": "2650", "stop_loss": "2642.5", "exit_price": "2665", "initial_risk_amount": "50",
         "net_pl": "100", "rule_compliance": "followed", "setup": "S-001"}


class TrackerBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tos-")
        os.environ["TOS_DATA_DIR"] = self.dir

    def tearDown(self):
        os.environ.pop("TOS_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)


class Records(TrackerBase):
    def test_files_created_from_templates(self):
        tt.ensure_files()
        for kind, (rel, _) in tt.KINDS.items():
            self.assertTrue(os.path.exists(os.path.join(self.dir, rel)), kind)
        self.assertEqual(tt.records("trades")["header"][0], "trade_id")

    def test_trade_append_normalises_and_derives_r(self):
        row = tt.append("trades", dict(TRADE))
        self.assertTrue(row["trade_id"].startswith("T-"))
        self.assertEqual((row["account_type"], row["direction"]), ("DEMO", "BUY"))
        self.assertEqual(row["r_multiple"], "2.00")          # 100 / 50
        self.assertTrue(row["recorded_at_utc"])

    def test_net_pl_derived_from_gross_and_costs(self):
        r = dict(TRADE, net_pl="", gross_pl="110", commission="7", swap="3")
        self.assertEqual(tt.append("trades", r)["net_pl"], "100.00")

    def test_rejections(self):
        cases = [
            (dict(TRADE, stop_loss=""), "missing"),
            (dict(TRADE, initial_risk_amount="0"), "positive"),
            (dict(TRADE, entry_price="abc"), "number"),
            (dict(TRADE, direction="LONGISH"), "direction"),
            (dict(TRADE, rule_compliance="VIOLATED"), "violation_detail"),
            (dict(TRADE, hacked_field="x"), "unknown fields"),
        ]
        for row, needle in cases:
            with self.assertRaises(tt.TrackerError) as cm:
                tt.append("trades", row)
            self.assertIn(needle, str(cm.exception))
        with self.assertRaises(tt.TrackerError):
            tt.append("../../etc/passwd", {})

    def test_duplicate_trade_id_refused(self):
        tt.append("trades", dict(TRADE, trade_id="X1"))
        with self.assertRaises(tt.TrackerError):
            tt.append("trades", dict(TRADE, trade_id="X1"))

    def test_edit_outside_tracker_blocks_writes(self):
        tt.append("trades", dict(TRADE, trade_id="X1", net_pl="-50"))
        path = os.path.join(self.dir, tt.KINDS["trades"][0])
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        rows[1][rows[0].index("net_pl")] = "50"            # turn a loss into a win
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
        self.assertTrue(tt.integrity_problems())
        with self.assertRaises(tt.TrackerError) as cm:
            tt.append("trades", dict(TRADE))
        self.assertIn("integrity", str(cm.exception))

    def test_payout_received_needs_amount_and_pending_is_not_cash(self):
        base = {"account_label": "FTMO-1", "firm": "FTMO", "requested_date": "2026-12-01",
                "amount_requested": "800", "currency": "USD"}
        with self.assertRaises(tt.TrackerError):
            tt.append("payouts", dict(base, status="RECEIVED"))
        tt.append("payouts", dict(base, status="REQUESTED"))
        econ = tt.summary()["prop_economics"]
        self.assertEqual(econ["payouts_received_by_currency"], {})
        self.assertEqual(econ["payouts_pending_NOT_CASH"], {"USD": 800.0})

    def test_export_neutralises_formulas(self):
        tt.append("trades", dict(TRADE, lessons="=HYPERLINK(\"http://x\")", net_pl="-25"))
        text = tt.export_csv("trades").decode()
        self.assertIn("'=HYPERLINK", text)
        self.assertIn(",-25,", text)                       # negative numbers untouched


class PropAndCalculators(TrackerBase):
    def test_snapshot_runs_firm_rules(self):
        tt.append("snapshots", {"account_label": "FTMO-2S-100K", "firm": "FTMO", "rules_preset": "FTMO_2STEP",
                                "stage": "EVALUATION", "initial_balance": "100000", "equity": "96500",
                                "day_start_balance": "98900"})
        acct = tt.summary()["prop_accounts"][0]
        self.assertEqual(acct["daily_floor"], 93900)
        self.assertEqual(acct["internal_daily_stop"], 96900)
        self.assertEqual(acct["status"], "WARN")

    def test_unknown_preset_refused(self):
        with self.assertRaises(tt.TrackerError):
            tt.append("snapshots", {"account_label": "A", "rules_preset": "MADE_UP", "stage": "EVALUATION",
                                    "initial_balance": "1", "equity": "1", "day_start_balance": "1"})

    def test_size_and_drawdown(self):
        r = tt.size({"equity": "10000", "risk_pct": "0.5", "symbol": "XAUUSD", "entry": "2650",
                     "stop": "2642.5", "spread": "0.3", "slippage": "0.1"})
        self.assertEqual(r["lots"], 0.06)
        with self.assertRaises(tt.TrackerError):
            tt.size({"equity": "10000", "risk_pct": "3", "entry": "2650", "stop": "2640"})
        d = tt.drawdown({"preset": "FTMO_2STEP", "initial": "100000", "equity": "95000",
                         "day_start_balance": "98900"})
        self.assertEqual(d["status"], "CRITICAL")


class PaperSync(TrackerBase):
    TICKETS = {"XAUUSD": [
        {"id": 1, "opened": 1000, "filled": 1010, "closed": 2000, "dir": "LONG", "model": "PULLBACK",
         "entry": 2650.0, "sl": 2640.0, "tp1": 2665.0, "exit": 2665.0, "gross_r": 0.75, "cost_r": 0.03,
         "r": 0.72, "outcome": "TP1-BE", "grade": "A", "conf": 72, "mfe_r": 1.6, "mae_r": -0.2},
        {"id": 2, "opened": 3000, "closed": 4000, "dir": "SHORT", "model": "BREAKOUT",
         "entry": 2660.0, "sl": 2670.0, "tp1": 2645.0, "exit": 2670.5, "gross_r": -1.05, "cost_r": 0.03,
         "r": -1.08, "outcome": "SL"},
        {"id": 3, "opened": 5000, "dir": "LONG"},            # still open: not imported
    ]}

    def test_sync_is_idempotent_and_in_r(self):
        self.assertEqual(tt.sync_paper(self.TICKETS), 2)
        self.assertEqual(tt.sync_paper(self.TICKETS), 0)
        rows = tt.read("trades")
        self.assertEqual({r["account_type"] for r in rows}, {"PAPER"})
        self.assertEqual([r["direction"] for r in rows], ["BUY", "SELL"])
        m = tt.summary()["trades"]["practice"]["metrics"]
        self.assertEqual(m["n"], 2)
        self.assertAlmostEqual(m["expectancy_r"], (0.72 - 1.08) / 2, places=3)
        self.assertEqual(tt.summary()["trades"]["personal"]["metrics"]["n"], 0)


class NetWorthAndWorkbook(TrackerBase):
    def _seed(self):
        tt.append("networth", {"date": "2026-11-01", "item": "M-Pesa", "category": "cash", "amount": "25000", "currency": "KES", "liquid": "y"})
        tt.append("networth", {"date": "2026-11-01", "item": "MMF", "category": "EMERGENCY_FUND", "amount": "300000", "currency": "KES", "liquid": "Y"})
        tt.append("networth", {"date": "2026-11-10", "item": "MMF", "category": "EMERGENCY_FUND", "amount": "320000", "currency": "KES", "liquid": "Y"})
        tt.append("networth", {"date": "2026-11-01", "item": "Car loan", "category": "LOAN", "amount": "150000", "currency": "KES"})
        tt.append("deposits", {"date": "2026-11-02", "account": "Broker KE", "amount": "500", "currency": "USD",
                               "fx_rate_to_kes": "129", "fx_rate_source": "ACTUAL"})
        tt.append("reconciliation", {"period_end": "2026-11-30", "account": "Broker KE", "currency": "USD",
                                     "opening_equity": "0", "deposits": "500", "realised_pl": "40", "closing_equity_statement": "540"})
        tt.append("snapshots", {"account_label": "FTMO-1", "rules_preset": "FTMO_2STEP", "stage": "EVALUATION",
                                "initial_balance": "100000", "equity": "99000", "day_start_balance": "100000"})

    def test_net_worth_latest_values_liabilities_and_broker_equity(self):
        self._seed()
        nw = tt.net_worth()
        # 25,000 + 320,000 (latest MMF) + 540 x 129 broker equity - 150,000 loan
        self.assertEqual(nw["assets_kes"], 25000 + 320000 + 540 * 129)
        self.assertEqual(nw["liabilities_kes"], 150000)
        self.assertEqual(nw["net_worth_kes"], 25000 + 320000 + 540 * 129 - 150000)
        self.assertEqual(nw["liquid_net_worth_kes"], nw["net_worth_kes"])
        # prop nominal balance (100,000) never enters net worth
        self.assertLess(nw["assets_kes"], 500000)

    def test_net_worth_rejections(self):
        with self.assertRaises(tt.TrackerError):
            tt.append("networth", {"date": "2026-11-01", "item": "X", "category": "CRYPTO_MOON", "amount": "1", "currency": "KES"})
        with self.assertRaises(tt.TrackerError):
            tt.append("networth", {"date": "2026-11-01", "item": "X", "category": "LOAN", "amount": "-5", "currency": "KES"})
        with self.assertRaises(tt.TrackerError):
            tt.append("networth", {"date": "2026-11-01", "item": "X", "category": "CASH", "amount": "5", "currency": "USD"})

    def test_workbook_has_sheets_and_live_formulas(self):
        import zipfile, io
        import tracker_workbook
        self._seed()
        data = tracker_workbook.build()
        z = zipfile.ZipFile(io.BytesIO(data))
        wbxml = z.read("xl/workbook.xml").decode()
        for name in ("Summary", "NetWorth", "Trades", "Prop", "Capital", "Reconciliation", "ReadMe"):
            self.assertIn(f'name="{name}"', wbxml)
        self.assertIn('fullCalcOnLoad="1"', wbxml)
        nw_sheet = z.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn("<f>", nw_sheet)
        self.assertIn("Car loan", nw_sheet)
        summary = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("NetWorth!$I$", summary)
        try:
            import openpyxl
        except ImportError:
            return
        wb = openpyxl.load_workbook(io.BytesIO(data))
        self.assertEqual(wb.sheetnames[0], "Summary")


class BrokerFills(TrackerBase):
    FILLS = [
        {"broker_trade_id": "1001", "instrument": "xauusd", "direction": "long", "volume": "0.10",
         "entry_price": "2650.0", "exit_price": "2660.0", "open_time_utc": "2026-11-13T13:00:00+00:00",
         "close_time_utc": "2026-11-13T14:00:00+00:00", "gross_pl": "100", "commission": "0.7", "currency": "USD"},
        {"broker_trade_id": "1002", "instrument": "XAUUSD", "direction": "SELL", "volume": "0.10",
         "entry_price": "2660.0", "exit_price": "2665.0", "stop_loss": "2665.0", "net_pl": "-50",
         "initial_risk_amount": "50", "close_time_utc": "2026-11-13T15:00:00+00:00", "currency": "USD"},
    ]

    def test_import_is_idempotent_and_risky_fills_wait_in_inbox(self):
        r = tt.import_fills("tradingview", "Pepperstone", "PEP-TV-1", "personal", self.FILLS)
        self.assertEqual((r["added"], r["duplicates"], r["auto_journaled"]), (2, 0, 1))
        again = tt.import_fills("tradingview", "Pepperstone", "PEP-TV-1", "PERSONAL", self.FILLS)
        self.assertEqual((again["added"], again["duplicates"]), (0, 2))
        pending = tt.pending_fills()
        self.assertEqual([f["broker_trade_id"] for f in pending], ["1001"])     # no stop yet
        self.assertEqual(pending[0]["direction"], "BUY")
        trades = tt.read("trades")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["trade_id"], tt.fill_key("tradingview", "Pepperstone", "1002"))
        self.assertEqual(trades[0]["r_multiple"], "-1.00")

    def test_filing_a_fill_needs_risk_and_derives_net(self):
        tt.import_fills("tradingview", "Pepperstone", "PEP-TV-1", "PERSONAL", self.FILLS[:1])
        fid = tt.pending_fills()[0]["fill_id"]
        with self.assertRaises(tt.TrackerError):
            tt.journal_fill(fid, {"setup": "S-001"})                    # no stop or risk
        with self.assertRaises(tt.TrackerError):
            tt.journal_fill(fid, {"hacked": "x"})
        row = tt.journal_fill(fid, {"stop_loss": "2645", "initial_risk_amount": "50", "setup": "S-001",
                                    "emotional_state": "Calm"})
        self.assertEqual(row["net_pl"], "99.30")                        # 100 - 0.7 commission
        self.assertEqual(row["r_multiple"], "1.99")
        self.assertEqual(tt.pending_fills(), [])
        with self.assertRaises(tt.TrackerError):
            tt.journal_fill(fid, {"stop_loss": "2645", "initial_risk_amount": "50"})

    def test_ignore_removes_from_inbox(self):
        tt.import_fills("tradingview", "Pepperstone", "PEP-TV-1", "PERSONAL", self.FILLS[:1])
        fid = tt.pending_fills()[0]["fill_id"]
        st, _, body, _ = tt.handle("POST", "/api/tracker/fills/ignore", {}, {"fill_id": fid})
        self.assertEqual(st, 200)
        self.assertEqual(tt.pending_fills(), [])
        st, _, body, _ = tt.handle("POST", "/api/tracker/fills/ignore", {}, {"fill_id": "nope"})
        self.assertEqual(st, 400)

    def test_import_rejects_bad_account_type(self):
        st, _, body, _ = tt.handle("POST", "/api/tracker/import/fills", {}, {"broker": "X", "account_type": "MOON", "fills": []})
        self.assertEqual(st, 400)


class HttpRoutes(TrackerBase):
    """End to end through the desk's real HTTP handler, without the market poller."""

    @classmethod
    def setUpClass(cls):
        import xauusd_trader as sd
        from http.server import ThreadingHTTPServer
        cls.sd = sd
        os.environ.pop("SIGNALDESK_PASS", None)
        sd.AUTH_PASS = None
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), sd.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def _post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_page_summary_append_export(self):
        status, ctype, body = self._get("/tracker")
        self.assertEqual(status, 200)
        self.assertIn(b"Trading Tracker", body)
        status, data = self._post("/api/tracker/append", {"kind": "trades", "row": TRADE})
        self.assertEqual((status, data["ok"]), (200, True))
        status, data = self._post("/api/tracker/append", {"kind": "trades", "row": {"instrument": "X"}})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])
        _, _, body = self._get("/api/tracker/summary")
        summary = json.loads(body)
        self.assertEqual(summary["trades"]["practice"]["metrics"]["n"], 1)
        _, ctype, body = self._get("/api/tracker/export?kind=trades")
        self.assertTrue(ctype.startswith("text/csv"))
        _, ctype, body = self._get("/api/tracker/workbook.xlsx")
        self.assertTrue(ctype.startswith("application/vnd.openxmlformats"))
        self.assertEqual(body[:2], b"PK")
        _, ctype, _ = self._get("/api/tracker/backup")
        self.assertEqual(ctype, "application/zip")

    def test_existing_routes_untouched(self):
        status, _, body = self._get("/api/health")
        self.assertEqual(status, 200)
        self.assertIn(b'"engine"', body)


if __name__ == "__main__":
    unittest.main()
