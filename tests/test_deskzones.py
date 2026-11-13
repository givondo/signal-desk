"""Rule-based time zone fallback must match the IANA database exactly.

Run: python -m unittest discover -s tests
"""
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import deskzones as dz  # noqa: E402

KEYS = ("Africa/Nairobi", "Europe/London", "America/New_York")


class FallbackMatchesTzdata(unittest.TestCase):
    def test_utc_to_local_every_hour_2025_to_2028(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for key in KEYS:
            real, fb = ZoneInfo(key), dz.fallback_zone(key)
            t = start
            while t.year < 2029:
                a, b = t.astimezone(real), t.astimezone(fb)
                self.assertEqual(a.replace(tzinfo=None), b.replace(tzinfo=None), f"{key} {t}")
                t += timedelta(hours=1)

    def test_session_wall_times_to_utc(self):
        # the analyst builds 08:00 London / 09:30 New York wall times every day
        for key, (h, m) in (("Europe/London", (8, 0)), ("America/New_York", (9, 30))):
            real, fb = ZoneInfo(key), dz.fallback_zone(key)
            d = datetime(2025, 1, 1)
            while d.year < 2029:
                a = datetime(d.year, d.month, d.day, h, m, tzinfo=real).astimezone(timezone.utc)
                b = datetime(d.year, d.month, d.day, h, m, tzinfo=fb).astimezone(timezone.utc)
                self.assertEqual(a, b, f"{key} {d.date()}")
                d += timedelta(days=1)


class StartsWithoutTzdata(unittest.TestCase):
    def test_desk_imports_when_zoneinfo_has_no_data(self):
        code = (
            "import zoneinfo, sys\n"
            "def broken(key): raise zoneinfo.ZoneInfoNotFoundError(key)\n"
            "zoneinfo.ZoneInfo = broken\n"
            f"sys.path.insert(0, {ROOT!r})\n"
            "import xauusd_trader as sd, precifarm_analyst as pa\n"
            "assert sd.DESK_TZ is not None and sd._NY is not None\n"
            "print(pa.session_clock_lines()[0])\n"
        )
        env = dict(os.environ, DATA_DIR=os.path.join(ROOT, ".tz-test-data"))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("EAT", r.stdout)


if __name__ == "__main__":
    unittest.main()
