"""Trader research registry tests. Stdlib only."""
import copy
import io
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import trader_research as tr  # noqa: E402


class Registry(unittest.TestCase):
    def setUp(self):
        self.data = tr.load()

    def test_registry_is_valid(self):
        errors, _ = tr.check(self.data, today=date(2026, 11, 15))
        self.assertEqual(errors, [])

    def test_every_coded_setup_is_linked(self):
        linked = {c for t in self.data["traders"] for c in t["strategy_codes"]}
        coded = {c for c in tr.known_codes() if c.startswith("S-") and "+" not in c}
        self.assertTrue(coded <= linked, coded - linked)

    def test_bad_entries_are_caught(self):
        d = copy.deepcopy(self.data)
        d["traders"][0]["record_source"] = "http://insecure.example"
        d["traders"][1]["strategy_codes"] = ["S-999"]
        d["traders"][2]["verification"] = "Z"
        d["traders"].append(dict(d["traders"][3]))
        errors, _ = tr.check(d, today=date(2026, 11, 15))
        text = " ".join(errors)
        for needle in ("https", "S-999", "verification", "duplicate"):
            self.assertIn(needle, text)

    def test_stale_and_csv(self):
        self.assertEqual(tr.stale(self.data, 7, today=date(2026, 11, 16)), [])
        self.assertEqual(len(tr.stale(self.data, 7, today=date(2026, 12, 31))), len(self.data["traders"]))
        buf = io.StringIO()
        tr.to_csv(self.data, buf)
        lines = buf.getvalue().strip().splitlines()
        self.assertEqual(len(lines), len(self.data["traders"]) + 1)
        self.assertTrue(lines[0].startswith("id,name"))


if __name__ == "__main__":
    unittest.main()
