"""Trader research tracker: validate, list stale entries, export for the Sheet.

    py tools/trader_research.py check          # schema, sources, strategy codes; exit 1 on errors
    py tools/trader_research.py stale --days 7  # entries not re-checked within N days
    py tools/trader_research.py csv > traders.csv

The registry is trading_os/14_Trader_Research/traders.json. The weekly research task
re-reads each record_source, updates figures with the date read, and runs `check`.
Stdlib only.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "trading_os" / "14_Trader_Research" / "traders.json"
CHAMPIONS = ROOT / "trading_os" / "05_Backtesting" / "gtos_backtest" / "champions.py"
REQUIRED = ["id", "name", "category", "markets", "record", "verification", "record_period", "record_source",
            "method", "strategy_codes", "xauusd_relevance", "status", "last_checked"]
NON_SETUP_CODES = {"P-01", "F-01"}
COLUMNS = ["id", "name", "category", "markets", "record", "verification", "record_period", "method",
           "strategy_codes", "xauusd_relevance", "status", "last_checked", "record_source", "method_source", "notes"]


def load(path: Path = REGISTRY) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def known_codes() -> set[str]:
    text = CHAMPIONS.read_text(encoding="utf-8") if CHAMPIONS.exists() else ""
    return set(re.findall(r'"(S-\d{3}(?:\+PTJ)?)":', text)) | NON_SETUP_CODES


def check(data: dict, today: date | None = None) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    levels = set(data.get("verification_levels", {}))
    codes = known_codes()
    seen = set()
    today = today or date.today()
    for t in data.get("traders", []):
        tid = t.get("id", "?")
        for k in REQUIRED:
            if t.get(k) in (None, ""):
                errors.append(f"{tid}: missing {k}")
        if tid in seen:
            errors.append(f"{tid}: duplicate id")
        seen.add(tid)
        if t.get("verification") not in levels:
            errors.append(f"{tid}: verification must be one of {sorted(levels)}")
        for k in ("record_source", "method_source"):
            v = t.get(k) or ""
            if v and not v.startswith("https://"):
                errors.append(f"{tid}: {k} must be an https link")
        for c in t.get("strategy_codes") or []:
            if c not in codes:
                errors.append(f"{tid}: strategy code {c} not found in champions.py")
        try:
            checked = datetime.strptime(t.get("last_checked", ""), "%Y-%m-%d").date()
            if checked > today:
                errors.append(f"{tid}: last_checked is in the future")
        except ValueError:
            errors.append(f"{tid}: last_checked must be YYYY-MM-DD")
        if t.get("verification") in ("A", "B") and not t.get("record_source"):
            errors.append(f"{tid}: audited or certified records need a record_source")
        if t.get("verification") == "D" and t.get("strategy_codes"):
            warnings.append(f"{tid}: strategy coded from a self-reported lead; keep it at the back of the queue")
    return errors, warnings


def stale(data: dict, days: int, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for t in data.get("traders", []):
        try:
            age = (today - datetime.strptime(t["last_checked"], "%Y-%m-%d").date()).days
        except (KeyError, ValueError):
            age = 10 ** 6
        if age > days:
            out.append({"id": t.get("id"), "name": t.get("name"), "days": age})
    return out


def to_csv(data: dict, fh) -> None:
    w = csv.writer(fh)
    w.writerow(COLUMNS)
    for t in data.get("traders", []):
        w.writerow([", ".join(t.get(c) or []) if c == "strategy_codes" else t.get(c, "") for c in COLUMNS])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    s = sub.add_parser("stale")
    s.add_argument("--days", type=int, default=7)
    sub.add_parser("csv")
    a = ap.parse_args(argv)
    data = load()
    if a.cmd == "check":
        errors, warnings = check(data)
        for w in warnings:
            print("warning:", w)
        for e in errors:
            print("error:", e)
        print(f"{len(data.get('traders', []))} traders, {len(errors)} errors, {len(warnings)} warnings")
        return 1 if errors else 0
    if a.cmd == "stale":
        rows = stale(data, a.days)
        for r in rows:
            print(f"{r['id']} {r['name']}: {r['days']} days since last check")
        return 0
    to_csv(data, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
