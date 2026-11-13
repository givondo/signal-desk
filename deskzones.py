"""Time zones for Signal Desk that work with or without the IANA tz database.

Windows Python ships without tz data, so ZoneInfo("Africa/Nairobi") fails
unless the `tzdata` package is installed. This module returns ZoneInfo when
it is available and otherwise falls back to exact rule-based zones:

  * Africa/Nairobi: UTC+3, no daylight saving.
  * Europe/London: GMT, BST from 01:00 UTC on the last Sunday of March to
    01:00 UTC on the last Sunday of October (EU rule).
  * America/New_York: EST, EDT from 02:00 local on the second Sunday of March
    to 02:00 local on the first Sunday of November (US rule since 2007).

Stdlib only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

_HOUR = timedelta(hours=1)


def _nth_sunday(year: int, month: int, n: int) -> datetime:
    d = datetime(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_sunday(year: int, month: int) -> datetime:
    nxt = datetime(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() + 1) % 7)


class _RuleZone(tzinfo):
    """Fixed standard offset plus a one-hour summer shift between two UTC instants."""

    def __init__(self, key: str, std_hours: int, std_name: str, dst_name: str, rule):
        self.key, self._std = key, timedelta(hours=std_hours)
        self._names = (std_name, dst_name)
        self._rule = rule          # year -> (start_utc, end_utc) naive UTC datetimes

    def _is_dst_utc(self, u: datetime) -> bool:
        start, end = self._rule(u.year)
        return start <= u < end

    def utcoffset(self, dt):
        return self._std + self.dst(dt)

    def dst(self, dt):
        if dt is None:
            return timedelta(0)
        u = dt.replace(tzinfo=None) - self._std     # wall time read as standard time
        return _HOUR if self._is_dst_utc(u) else timedelta(0)

    def tzname(self, dt):
        return self._names[bool(self.dst(dt))]

    def fromutc(self, dt):
        u = dt.replace(tzinfo=None)
        off = self._std + (_HOUR if self._is_dst_utc(u) else timedelta(0))
        return (u + off).replace(tzinfo=self)

    def __repr__(self):
        return f"RuleZone({self.key!r})"


def _london_rule(y):
    return _last_sunday(y, 3).replace(hour=1), _last_sunday(y, 10).replace(hour=1)


def _new_york_rule(y):
    # 02:00 EST = 07:00 UTC in March; 02:00 EDT = 06:00 UTC in November
    return _nth_sunday(y, 3, 2).replace(hour=7), _nth_sunday(y, 11, 1).replace(hour=6)


_FALLBACKS = {
    "Africa/Nairobi": timezone(timedelta(hours=3), "EAT"),
    "Europe/London": _RuleZone("Europe/London", 0, "GMT", "BST", _london_rule),
    "America/New_York": _RuleZone("America/New_York", -5, "EST", "EDT", _new_york_rule),
}


def get_zone(key: str) -> tzinfo:
    """ZoneInfo(key) when tz data exists, else an exact rule-based fallback."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(key)
    except Exception:
        if key in _FALLBACKS:
            return _FALLBACKS[key]
        raise


def fallback_zone(key: str) -> tzinfo:
    return _FALLBACKS[key]
