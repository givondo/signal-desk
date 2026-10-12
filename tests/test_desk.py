"""Signal Desk engine tests. Stdlib only, no network.

Run: python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="sd-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import precifarm_analyst as pa  # noqa: E402
import xauusd_trader as sd  # noqa: E402

SYM = "XAUUSD"
SPREAD = 0.30   # gold spread_est; cost on a 10.0 stop = 0.03R
T = 1_000_000   # clock well past the opening cooldown


def sig(px, direction="LONG", entry=None, model="MARKET", sl_dist=10.0,
        grade="A", tradeable=True):
    entry = px if entry is None else entry
    sgn = 1 if direction == "LONG" else -1
    return {"status": "ok", "price": px, "direction": direction,
            "tradeable": tradeable, "grade": grade, "confidence": 70,
            "sl_dist": sl_dist,
            "levels": {"entry": entry, "sl": entry - sgn * sl_dist,
                       "tp1": entry + sgn * sd.TP_R[0] * sl_dist,
                       "tp2": entry + sgn * sd.TP_R[1] * sl_dist, "model": model}}


def tracker():
    path = os.path.join(os.environ["DATA_DIR"], f"t{os.urandom(4).hex()}.json")
    return sd.Tracker(path, SPREAD)


class TrackerExecution(unittest.TestCase):
    def test_tp1_then_breakeven_banks_half_of_tp1_net_of_spread(self):
        t = tracker()
        t.update(SYM, sig(2000), now=T)
        t.update(SYM, sig(2015.5), now=T + 60)        # TP1 at 1.5R: bank 50%
        self.assertEqual(t.active["state"], "runner")
        t.update(SYM, sig(1999.0), now=T + 120)       # back through entry
        p = t.resolved[-1]
        self.assertEqual(p["outcome"], "TP1-BE")
        self.assertEqual(p["gross_r"], 0.70)      # 0.5*1.5 + 0.5 * -0.1 slippage
        self.assertEqual(p["r"], round(0.70 - 0.03, 2))

    def test_tp2_pays_two_and_a_quarter_r(self):
        t = tracker()
        t.update(SYM, sig(2000), now=T)
        t.update(SYM, sig(2031), now=T + 60)
        self.assertEqual(t.resolved[-1]["outcome"], "TP2")
        self.assertEqual(t.resolved[-1]["gross_r"], 2.25)   # 0.5*1.5 + 0.5*3

    def test_targets_follow_protocol_minimum(self):
        models = sd.entry_models(SYM, "LONG", 2000.0, 8.0, {}, 70, None, "TRENDING")
        for m in models:
            self.assertAlmostEqual(abs(m["tp1"] - m["entry"]) / m["sl_dist"], 1.5, delta=0.01)

    def test_stop_gap_books_actual_loss_not_minus_one(self):
        t = tracker()
        t.update(SYM, sig(2000), now=T)
        t.update(SYM, sig(1985), now=T + 60)          # gapped 1.5R through stop
        self.assertEqual(t.resolved[-1]["gross_r"], -1.5)
        self.assertEqual(t.resolved[-1]["mae_r"], -1.5)

    def test_limit_waits_for_fill_and_cancels_when_signal_dies(self):
        t = tracker()
        t.update(SYM, sig(2000, entry=1995, model="PULLBACK"), now=T)
        self.assertEqual(t.active["state"], "pending")
        self.assertEqual(t.active["order"], "LIMIT")
        t.update(SYM, sig(2003, entry=1995, model="PULLBACK"), now=T + 60)
        self.assertEqual(t.active["state"], "pending")   # never touched 1995
        neutral = sig(2004, direction="LONG")
        neutral["direction"] = "NEUTRAL"
        t.update(SYM, neutral, now=T + 120)
        self.assertIsNone(t.active)
        self.assertEqual(t.cancelled, 1)
        self.assertEqual(t.resolved, [])                 # nothing booked
        self.assertEqual(t.perf()["fill_rate"], 0)

    def test_limit_fills_on_touch(self):
        t = tracker()
        t.update(SYM, sig(2000, entry=1995, model="PULLBACK"), now=T)
        t.update(SYM, sig(1994.8, entry=1995, model="PULLBACK"), now=T + 60)
        self.assertEqual(t.active["state"], "open")
        self.assertEqual(t.active["filled"], T + 60)

    def test_pending_order_expires(self):
        t = tracker()
        t.update(SYM, sig(2000, entry=1995, model="PULLBACK"), now=T)
        t.update(SYM, sig(2001, entry=1995, model="PULLBACK"),
                 now=T + sd.PENDING_TTL_S + 1)
        self.assertIsNone(t.active)
        self.assertEqual(t.cancelled, 1)

    def test_legacy_records_are_restated(self):
        t = tracker()
        legacy = {"id": 1, "dir": "LONG", "outcome": "TP1-BE", "r": 1.0,
                  "sl_dist": 10.0, "opened": 0, "closed": 60, "dur_min": 1}
        p = t._restate(dict(legacy))
        self.assertEqual(p["gross_r"], 0.5)
        self.assertEqual(p["r"], 0.47)
        runner_flip = dict(legacy, outcome="FLIP", r=0.4, tp1_ts=30)
        self.assertEqual(t._restate(runner_flip)["gross_r"], 0.7)


class Sessions(unittest.TestCase):
    gold = sd.SYMBOLS["XAUUSD"]

    def at(self, *a):
        return sd.market_session(self.gold, datetime(*a, tzinfo=timezone.utc))

    def test_weekend_closed(self):
        self.assertFalse(self.at(2026, 10, 11, 12, 0)["open"])      # Sunday
        self.assertFalse(self.at(2026, 10, 9, 21, 30)["open"])      # Fri 17:30 NY
        self.assertTrue(self.at(2026, 10, 11, 21, 30)["open"])      # Sun 17:30 NY

    def test_overlap_follows_daylight_saving(self):
        # Summer (BST/EDT): NY opens 12:00 UTC
        self.assertEqual(self.at(2026, 7, 15, 12, 30)["name"], "LDN/NY OVERLAP")
        # Winter (GMT/EST): NY opens 13:00 UTC, so 12:30 is London only
        self.assertEqual(self.at(2027, 1, 13, 12, 30)["name"], "LONDON")
        self.assertEqual(self.at(2027, 1, 13, 13, 30)["name"], "LDN/NY OVERLAP")

    def test_crypto_never_closes(self):
        s = sd.market_session(sd.SYMBOLS["BTCUSD"],
                              datetime(2026, 10, 11, 12, tzinfo=timezone.utc))
        self.assertTrue(s["open"])


class Governor(unittest.TestCase):
    def setUp(self):
        self.saved = dict(sd.trackers)
        for k in list(sd.trackers):
            sd.trackers[k] = tracker()

    def tearDown(self):
        sd.trackers.clear()
        sd.trackers.update(self.saved)

    def book(self, sym, r, at):
        sd.trackers[sym].resolved.append(
            {"id": len(sd.trackers[sym].resolved) + 1, "dir": "LONG",
             "outcome": "SL", "r": r, "opened": at - 60, "closed": at})

    def live_sig(self, grade="B"):
        return {"status": "ok", "direction": "LONG", "tradeable": True,
                "grade": grade, "session": {"open": True},
                "desk_action": {"mode": "EXECUTE"}}

    def test_loss_streak_tightens_to_a_grade(self):
        now = 1_800_000_000
        self.book("XAUUSD", -1.0, now - 200)
        self.book("EURUSD", -1.0, now - 100)
        gov = sd.desk_governor(now)
        self.assertEqual(gov["loss_streak"], 2)
        s = self.live_sig("B")
        sd.apply_governor("XAUUSD", s, gov)
        self.assertFalse(s["tradeable"])
        s = self.live_sig("A")
        sd.apply_governor("XAUUSD", s, gov)
        self.assertTrue(s["tradeable"])

    def test_daily_stop_halts_desk(self):
        now = 1_800_000_000
        for i, sym in enumerate(("XAUUSD", "EURUSD", "BTCUSD", "US30")):
            self.book(sym, -1.03, now - 400 + i)
        gov = sd.desk_governor(now)
        self.assertTrue(gov["halted"])
        s = self.live_sig("A+")
        sd.apply_governor("XAUUSD", s, gov)
        self.assertEqual(s["desk_action"]["mode"], "HALT")
        self.assertFalse(s["tradeable"])


class AnalystBrief(unittest.TestCase):
    def test_position_size_uses_symbol_contract_value(self):
        lots, _ = pa._position_size_lots(10_000, 0.5, 0.0020, 100_000)  # 20 pips
        self.assertEqual(lots, 0.25)
        lots, _ = pa._position_size_lots(10_000, 0.5, 5.0, 100)         # gold $5
        self.assertEqual(lots, 0.1)

    def test_clear_path_to_tp1(self):
        s = {"levels": {"entry": 2000, "tp1": 2010},
             "pivots": {"r1": 2005}, "day_high": 2020, "day_low": 1980}
        self.assertFalse(pa._clear_path_to_tp1(s))
        s["pivots"] = {"r1": 2015}
        self.assertTrue(pa._clear_path_to_tp1(s))


if __name__ == "__main__":
    unittest.main()
