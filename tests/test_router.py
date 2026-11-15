"""Portfolio router tests. Stdlib only, no network.

Run: python -m unittest discover -s tests
All signals, balances and trades here are synthetic test inputs, not trading records.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="sd-test-"))
sys.path.insert(0, ROOT)

import portfolio_router as pr  # noqa: E402
import trading_tracker as tt  # noqa: E402

LONG = {"symbol": "XAUUSD", "direction": "LONG", "entry": 4100.0, "stop": 4092.0, "tp1": 4112.0,
        "mode": "EXECUTE", "grade": "A", "spread": 0.3}
PERSONAL = {"label": "LIVE", "type": "PERSONAL", "status": "ACTIVE", "equity": 10000, "day_start_balance": 10000,
            "risk_pct": 0.25, "symbols": ["XAUUSD"], "currency": "USD"}
PROP = {"label": "FTMO", "type": "PROP", "status": "ACTIVE", "rules_preset": "FTMO_2STEP", "initial_balance": 100000,
        "equity": 100000, "day_start_balance": 100000, "risk_pct": 0.25, "symbols": ["XAUUSD"], "currency": "USD"}


def trade(label, pl, when="2026-11-15T09:00:00+00:00"):
    return {"account_type": "PERSONAL", "account_label": label, "instrument": "XAUUSD", "direction": "BUY",
            "entry_price": "4100", "stop_loss": "4092", "exit_price": "4099", "initial_risk_amount": "25",
            "net_pl": str(pl), "rule_compliance": "FOLLOWED", "exit_time_utc": when}


NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="router-")
        os.environ["TOS_DATA_DIR"] = self.dir

    def tearDown(self):
        os.environ.pop("TOS_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def one(self, signal, acc):
        return pr.route(signal, [dict(acc)], now=NOW)["accounts"][0]


class Routing(Base):
    def test_personal_ticket_sized_to_limit(self):
        a = self.one(LONG, PERSONAL)
        self.assertEqual(a["decision"], "TICKET")
        self.assertGreater(a["lots"], 0)
        self.assertLessEqual(a["money_at_risk"], 10000 * 0.25 / 100 + 1e-9)
        self.assertEqual(a["side"], "BUY")

    def test_risk_above_policy_is_capped_not_trusted(self):
        a = self.one(LONG, {**PERSONAL, "risk_pct": 0.9})
        self.assertEqual(a["risk_pct"], 0.25)
        self.assertLessEqual(a["money_at_risk"], 25.0 + 1e-9)

    def test_prop_sizes_from_initial_and_fits_internal_stop(self):
        a = self.one(LONG, PROP)
        self.assertEqual(a["decision"], "TICKET")
        self.assertLessEqual(a["money_at_risk"], 250.0 + 1e-9)
        self.assertEqual(a["drawdown"]["status"], "OK")

    def test_prop_breached_account_blocked(self):
        a = self.one(LONG, {**PROP, "equity": 89000, "day_start_balance": 90000})
        self.assertEqual(a["decision"], "BLOCKED")
        self.assertTrue(any("BREACHED" in r or "75%" in r for r in a["reasons"]))

    def test_prop_half_used_cuts_risk(self):
        a = self.one(LONG, {**PROP, "equity": 94500, "day_start_balance": 94500})
        self.assertEqual(a["risk_pct"], 0.125)

    def test_planned_and_paused_are_dry_runs(self):
        self.assertEqual(self.one(LONG, {**PROP, "status": "PLANNED"})["decision"], "DRY_RUN")
        self.assertEqual(self.one(LONG, {**PERSONAL, "status": "PAUSED"})["decision"], "DRY_RUN")

    def test_planned_prop_without_equity_uses_initial(self):
        acc = {k: v for k, v in PROP.items() if k not in ("equity", "day_start_balance")}
        a = self.one(LONG, {**acc, "status": "PLANNED"})
        self.assertEqual(a["decision"], "DRY_RUN")
        self.assertGreater(a["lots"], 0)

    def test_no_equity_blocks(self):
        acc = {k: v for k, v in PERSONAL.items() if k != "equity"}
        a = self.one(LONG, acc)
        self.assertEqual(a["decision"], "BLOCKED")

    def test_prepare_mode_is_watch(self):
        self.assertEqual(self.one({**LONG, "mode": "PREPARE"}, PERSONAL)["decision"], "WATCH")

    def test_bad_signals_block_everything(self):
        for bad in ({**LONG, "stop": 4105.0}, {**LONG, "stop": None}, {**LONG, "mode": "HALT"},
                    {**LONG, "direction": "NEUTRAL"}):
            a = self.one(bad, PERSONAL)
            self.assertEqual(a["decision"], "BLOCKED", bad)

    def test_symbol_not_allowed(self):
        a = self.one({**LONG, "symbol": "EURUSD", "entry": 1.1, "stop": 1.098}, PERSONAL)
        self.assertEqual(a["decision"], "BLOCKED")

    def test_two_losses_today_stop_the_account(self):
        tt.append("trades", trade("LIVE", -20))
        tt.append("trades", trade("LIVE", -20, "2026-11-15T10:00:00+00:00"))
        a = self.one(LONG, PERSONAL)
        self.assertEqual(a["decision"], "BLOCKED")
        self.assertTrue(any("losses in a row" in r for r in a["reasons"]))

    def test_daily_loss_limit(self):
        tt.append("trades", trade("LIVE", -60))
        a = self.one(LONG, PERSONAL)
        self.assertTrue(any("daily loss" in r for r in a["reasons"]))

    def test_yesterday_does_not_count_today(self):
        tt.append("trades", trade("LIVE", -60, "2026-11-14T09:00:00+00:00"))
        tt.append("trades", trade("LIVE", -20, "2026-11-14T10:00:00+00:00"))
        a = self.one(LONG, PERSONAL)
        self.assertEqual(a["today"]["losses_in_a_row_today"], 0)
        self.assertNotIn("daily", " ".join(a["reasons"]))

    def test_cross_prop_note(self):
        r = pr.route(LONG, [dict(PROP), {**PROP, "label": "FTMO-2"}], now=NOW)
        self.assertTrue(any("copy-trading" in n for n in r["notes"]))


class Desk(Base):
    def test_from_desk_payload(self):
        sig = {"status": "ok", "direction": "SHORT", "grade": "A", "confidence": 64, "signal_quality": 70,
               "spread_est": 0.3, "price": 4100, "desk_action": {"mode": "EXECUTE"},
               "levels": {"entry": 4100.0, "sl": 4108.0, "tp1": 4088.0, "tp2": 4076.0, "model": "MARKET"}}
        s = pr.from_desk("XAUUSD", sig)
        a = self.one(s, PERSONAL)
        self.assertEqual(a["decision"], "TICKET")
        self.assertEqual(a["side"], "SELL")

    def test_from_desk_rejects_error(self):
        with self.assertRaises(pr.RouterError):
            pr.from_desk("XAUUSD", {"status": "error"})


class Storage(Base):
    def test_example_accounts_load_and_route(self):
        accs = pr.load_accounts()
        labels = {a["label"] for a in accs}
        self.assertEqual(labels, {"TV-PAPER", "DERIV-LIVE", "FTMO-2STEP", "THE5ERS-HS"})
        r = pr.route(LONG, now=NOW)
        by = {a["account"]: a["decision"] for a in r["accounts"]}
        self.assertEqual(by["DERIV-LIVE"], "BLOCKED")      # no equity yet, and paused
        self.assertEqual(by["FTMO-2STEP"], "DRY_RUN")      # planned
        self.assertNotIn("TICKET", [by["DERIV-LIVE"], by["FTMO-2STEP"], by["THE5ERS-HS"]])

    def test_update_account_and_decision_log(self):
        pr.load_accounts()
        acc = pr.update_account("TV-PAPER", {"equity": "10000", "day_start_balance": "10000"})
        self.assertEqual(acc["equity"], 10000.0)
        with self.assertRaises(pr.RouterError):
            pr.update_account("TV-PAPER", {"type": "PROP"})
        r = pr.route(LONG)
        paper = next(a for a in r["accounts"] if a["account"] == "TV-PAPER")
        self.assertEqual(paper["decision"], "TICKET")
        pr.record_decision({"route_id": r["route_id"], "account": "TV-PAPER", "decision": "taken"})
        with self.assertRaises(pr.RouterError):
            pr.record_decision({"route_id": "nope", "account": "TV-PAPER", "decision": "TAKEN"})
        csv_text = pr.signals_csv().decode()
        self.assertIn(r["route_id"], csv_text)
        self.assertIn("TV-PAPER:TAKEN", csv_text)

    def test_invalid_prop_preset_refused(self):
        with self.assertRaises(pr.RouterError):
            pr.save_accounts([{**PROP, "rules_preset": "MADE_UP"}])

    def test_http_handler(self):
        status, _, body, _ = pr.handle("POST", "/api/router/route", {}, dict(LONG))
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        status, _, body, _ = pr.handle("GET", "/api/router/route", {"sym": ["XAUUSD"]}, None,
                                       latest_signal=lambda s: {"status": "error"})
        self.assertEqual(status, 400)
        self.assertEqual(pr.handle("GET", "/api/router/nope", {}, None)[0], 404)

    def test_on_signal_alerts_once_and_skips_all_blocked(self):
        sent, original = [], pr._send_telegram
        pr._send_telegram = lambda text: sent.append(text)
        os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"] = "t", "c"
        try:
            pr.save_accounts([dict(PERSONAL)])
            sig = {"status": "ok", "direction": "LONG", "grade": "A", "spread_est": 0.3,
                   "desk_action": {"mode": "EXECUTE"},
                   "levels": {"entry": 4100.0, "sl": 4092.0, "tp1": 4112.0, "model": "MARKET"}}
            pr.on_signal("XAUUSD", sig)
            pr.on_signal("XAUUSD", sig)
            btc = {**sig, "levels": {"entry": 90000.0, "sl": 89000.0, "tp1": 91500.0}}
            pr.on_signal("BTCUSD", btc)          # every account blocks BTCUSD: no alert
            import time as _t
            _t.sleep(0.2)
            self.assertEqual(len(sent), 1)
            self.assertEqual(len(pr._read_log("routes.jsonl")), 2)
        finally:
            pr._send_telegram = original
            os.environ.pop("TELEGRAM_BOT_TOKEN")
            os.environ.pop("TELEGRAM_CHAT_ID")

    def test_preview_route_is_not_logged(self):
        sig = {"status": "ok", "direction": "LONG", "desk_action": {"mode": "EXECUTE"},
               "levels": {"entry": 4100.0, "sl": 4092.0, "tp1": 4112.0}}
        status, _, body, _ = pr.handle("GET", "/api/router/route", {"sym": ["XAUUSD"], "preview": ["1"]}, None,
                                       latest_signal=lambda s: sig)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["preview"])
        self.assertEqual(pr._read_log("routes.jsonl"), [])
        pr.handle("GET", "/api/router/route", {"sym": ["XAUUSD"]}, None, latest_signal=lambda s: sig)
        self.assertEqual(len(pr._read_log("routes.jsonl")), 1)

    def test_account_health(self):
        h = pr.account_health(dict(PERSONAL), now=NOW)
        self.assertEqual((h["status"], h["daily_used"], h["streak_max"]), ("OK", 0.0, 2))
        tt.append("trades", trade("LIVE", -30))
        h = pr.account_health(dict(PERSONAL), now=NOW)
        self.assertAlmostEqual(h["daily_used"], 0.6)
        self.assertEqual(h["status"], "WARN")
        p = pr.account_health({**PROP, "equity": 94000, "day_start_balance": 94000}, now=NOW)
        self.assertAlmostEqual(p["max_used"], 0.6)
        self.assertIn(p["status"], ("WARN", "CRITICAL"))
        self.assertEqual(pr.account_health({k: v for k, v in PERSONAL.items() if k != "equity"})["status"], "NO DATA")
        status, _, body, _ = pr.handle("GET", "/api/router/accounts", {}, None)
        self.assertIn("health", json.loads(body)["accounts"][0])

    def test_alert_text_mentions_every_account(self):
        r = pr.route(LONG, [dict(PERSONAL), dict(PROP)], now=NOW, log=False)
        t = pr.alert_text(r)
        self.assertIn("LIVE", t)
        self.assertIn("FTMO", t)
        self.assertIn("Place by hand", t)


if __name__ == "__main__":
    unittest.main()
