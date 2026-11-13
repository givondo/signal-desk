"""Performance, capital and prop-economics calculations for GIVONDO Trading OS.

Deterministic code calculates; AI only interprets. Standard library only, so it
runs on any Python 3.10+ install without extra packages.

Principles enforced here:
  * Prop firm money and personal money are never added together.
  * Only payouts with status RECEIVED count as cash. Pending stays pending.
  * Trading returns are separated from deposits.
  * Raw journal rows are append-only: verify_append_only() detects edits.
  * Small samples are labelled. No edge is declared below 100 trades.

Usage:
  python tos_analytics.py dashboard            # writes Performance_Dashboard/dashboard_<date>.md
  python tos_analytics.py metrics              # prints journal metrics as JSON
  python tos_analytics.py seal                 # records hashes of current journal rows
  python tos_analytics.py verify               # checks no sealed row was edited or deleted

Set TOS_DATA_DIR to read records from another folder with the same layout.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import os

ROOT = Path(__file__).resolve().parent.parent
# Records can live outside the code folder (for example the Signal Desk data
# volume) so personal figures never sit in a public repository.
DATA_ROOT = Path(os.environ.get("TOS_DATA_DIR") or ROOT)
JOURNAL = DATA_ROOT / "07_Trading_Journal" / "Trade_Journal.csv"
SEAL = DATA_ROOT / "07_Trading_Journal" / ".journal_seal.json"
EVALS = DATA_ROOT / "08_Prop_Firm_Accounts" / "Evaluations.csv"
PAYOUTS = DATA_ROOT / "08_Prop_Firm_Accounts" / "Payouts.csv"
DEPOSITS = DATA_ROOT / "09_Personal_Capital" / "Deposits.csv"
WITHDRAWALS = DATA_ROOT / "09_Personal_Capital" / "Withdrawals.csv"
RECON = DATA_ROOT / "09_Personal_Capital" / "Account_Reconciliation.csv"
DASH_DIR = DATA_ROOT / "11_Performance_Analytics" / "Performance_Dashboard"

MIN_SAMPLE_FOR_EDGE = 100


# ----------------------------------------------------------------- helpers
def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [row for row in csv.DictReader(fh) if any((v or "").strip() for v in row.values())]


def num(value, default: float | None = None) -> float | None:
    if value is None or str(value).strip() == "":
        return default
    return float(str(value).replace(",", ""))


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ----------------------------------------------------------------- journal
REQUIRED_TRADE_FIELDS = ("trade_id", "account_type", "instrument", "direction", "entry_price",
                         "stop_loss", "exit_price", "initial_risk_amount", "net_pl")


def validate_trades(trades: list[dict]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for i, t in enumerate(trades, start=2):
        for f in REQUIRED_TRADE_FIELDS:
            if not (t.get(f) or "").strip():
                problems.append(f"row {i}: missing {f}")
        tid = t.get("trade_id", "")
        if tid in seen:
            problems.append(f"row {i}: duplicate trade_id {tid}")
        seen.add(tid)
        risk = num(t.get("initial_risk_amount"))
        if risk is not None and risk <= 0:
            problems.append(f"row {i}: initial_risk_amount must be positive (no stop means no trade)")
        stated_r = num(t.get("r_multiple"))
        pl = num(t.get("net_pl"))
        if stated_r is not None and risk and pl is not None and abs(stated_r - pl / risk) > 0.05:
            problems.append(f"row {i}: r_multiple {stated_r} does not match net_pl/risk {pl / risk:.2f}")
    return problems


def _max_drawdown(pnls: list[float], start_equity: float | None = None) -> tuple[float, float | None]:
    equity = start_equity or 0.0
    peak = equity
    mdd = 0.0
    mdd_pct = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        mdd = max(mdd, dd)
        if start_equity and peak > 0:
            mdd_pct = max(mdd_pct, dd / peak * 100.0)
    return mdd, (mdd_pct if start_equity else None)


def trade_metrics(trades: list[dict], start_equity: float | None = None) -> dict:
    """Core statistics on NET results (after commission and swap)."""
    trades = sorted(trades, key=lambda t: (t.get("exit_time_utc") or "", t.get("trade_id") or ""))
    pnls = [num(t["net_pl"], 0.0) for t in trades]
    rs = [num(t["net_pl"], 0.0) / num(t["initial_risk_amount"]) for t in trades
          if num(t.get("initial_risk_amount"))]
    n = len(pnls)
    if n == 0:
        return {"n": 0, "note": "No trades recorded yet."}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    win_rate = len(wins) / n
    loss_rate = len(losses) / n
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    expectancy = win_rate * avg_win - loss_rate * avg_loss
    mean_r = statistics.fmean(rs) if rs else None
    sd_r = statistics.stdev(rs) if len(rs) > 1 else None
    downside = [min(r, 0.0) for r in rs]
    dsd = math.sqrt(sum(d * d for d in downside) / len(downside)) if rs else None
    se = sd_r / math.sqrt(len(rs)) if sd_r else None
    lower95 = mean_r - 1.96 * se if (mean_r is not None and se) else None

    streak = max_streak = 0
    for p in pnls:
        streak = streak + 1 if p < 0 else 0
        max_streak = max(max_streak, streak)
    mdd, mdd_pct = _max_drawdown(pnls, start_equity)
    net = sum(pnls)
    holds = []
    for t in trades:
        a, b = _parse_dt(t.get("entry_time_utc", "")), _parse_dt(t.get("exit_time_utc", ""))
        if a and b:
            holds.append((b - a).total_seconds() / 3600.0)

    if n < 30:
        verdict = "Insufficient sample (under 30 trades). Statistics are descriptive only."
    elif n < MIN_SAMPLE_FOR_EDGE:
        verdict = f"Developing sample ({n} trades). No edge claim before {MIN_SAMPLE_FOR_EDGE}."
    elif lower95 is not None and lower95 > 0:
        verdict = "Credible edge: lower 95% bound of mean R is above zero."
    else:
        verdict = "No credible edge yet: lower 95% bound of mean R is at or below zero."

    return {
        "n": n, "wins": len(wins), "losses": len(losses), "breakeven": n - len(wins) - len(losses),
        "win_rate": round(win_rate, 4), "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "payoff_ratio": round(avg_win / avg_loss, 3) if avg_loss else None,
        "expectancy_per_trade": round(expectancy, 2),
        "expectancy_r": None if mean_r is None else round(mean_r, 3),
        "expectancy_r_lower95": None if lower95 is None else round(lower95, 3),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
        "gross_profit": round(gross_profit, 2), "gross_loss": round(gross_loss, 2), "net_pl": round(net, 2),
        "max_drawdown": round(mdd, 2), "max_drawdown_pct": None if mdd_pct is None else round(mdd_pct, 2),
        "recovery_factor": round(net / mdd, 3) if mdd else None,
        "sharpe_per_trade_r": round(mean_r / sd_r, 3) if (mean_r is not None and sd_r) else None,
        "sortino_per_trade_r": round(mean_r / dsd, 3) if (mean_r is not None and dsd) else None,
        "max_consecutive_losses": max_streak,
        "avg_holding_hours": round(statistics.fmean(holds), 2) if holds else None,
        "verdict": verdict,
    }


def breakdown(trades: list[dict], field: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[(t.get(field) or "UNLABELLED").strip()].append(t)
    return {k: trade_metrics(v) for k, v in sorted(groups.items())}


def compliance(trades: list[dict]) -> dict:
    n = len(trades)
    violated = [t for t in trades if (t.get("rule_compliance") or "").upper() == "VIOLATED"]
    return {"trades": n, "violations": len(violated),
            "compliance_rate": round(1 - len(violated) / n, 4) if n else None,
            "net_pl_on_violations": round(sum(num(t["net_pl"], 0.0) for t in violated), 2)}


# ----------------------------------------------------------------- append-only seal
def _row_hash(row: list[str]) -> str:
    return hashlib.sha256("\x1f".join(row).encode("utf-8")).hexdigest()


def seal_journal(journal: Path = JOURNAL, seal: Path = SEAL) -> int:
    with open(journal, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    data = {"sealed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "header": rows[0], "row_hashes": [_row_hash(r) for r in rows[1:]]}
    seal.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return len(rows) - 1


def verify_append_only(journal: Path = JOURNAL, seal: Path = SEAL) -> list[str]:
    if not seal.exists():
        return ["No seal yet. Run: python tos_analytics.py seal"]
    sealed = json.loads(seal.read_text(encoding="utf-8"))
    with open(journal, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    problems = []
    if rows[0] != sealed["header"]:
        problems.append("Header changed since seal.")
    current = [_row_hash(r) for r in rows[1:]]
    for i, h in enumerate(sealed["row_hashes"]):
        if i >= len(current):
            problems.append(f"Sealed row {i + 2} was deleted.")
        elif current[i] != h:
            problems.append(f"Sealed row {i + 2} was edited. Use Trade_Corrections.csv instead.")
    return problems


# ----------------------------------------------------------------- prop economics
def prop_economics(evaluations: list[dict], payouts: list[dict]) -> dict:
    fees_by_ccy: dict[str, float] = defaultdict(float)
    fees_kes = 0.0
    refunds = 0.0
    counts = defaultdict(int)
    for e in evaluations:
        ccy = e.get("fee_currency") or "USD"
        fees_by_ccy[ccy] += num(e.get("fee_paid"), 0.0)
        fees_kes += num(e.get("fee_kes"), 0.0)
        refunds += num(e.get("fee_refund_received"), 0.0)
        counts[(e.get("purchase_type") or "CHALLENGE").upper()] += 1
        counts["status_" + (e.get("status") or "UNKNOWN").upper()] += 1
    received = defaultdict(float)
    pending = defaultdict(float)
    received_kes = 0.0
    payout_fees = 0.0
    denied = 0
    for p in payouts:
        status = (p.get("status") or "").upper()
        ccy = p.get("currency") or "USD"
        if status == "RECEIVED":
            received[ccy] += num(p.get("amount_received"), 0.0)
            received_kes += num(p.get("kes_received"), 0.0)
            payout_fees += num(p.get("payout_fees"), 0.0)
        elif status in ("REQUESTED", "APPROVED"):
            pending[ccy] += num(p.get("amount_requested"), 0.0)
        elif status == "DENIED":
            denied += 1
    usd_net = received.get("USD", 0.0) - payout_fees + refunds - fees_by_ccy.get("USD", 0.0)
    total_fees_usd = fees_by_ccy.get("USD", 0.0)
    return {
        "fees_paid_by_currency": dict(fees_by_ccy), "fees_paid_kes": round(fees_kes, 2),
        "fee_refunds_received": round(refunds, 2), "purchase_counts": dict(counts),
        "payouts_received_by_currency": dict(received), "payouts_received_kes": round(received_kes, 2),
        "payouts_pending_NOT_CASH": dict(pending), "payouts_denied": denied,
        "payout_fees": round(payout_fees, 2),
        "net_usd_cash_result": round(usd_net, 2),
        "effective_payout_ratio": round(received.get("USD", 0.0) / total_fees_usd, 3) if total_fees_usd else None,
        "note": "Nominal account sizes are excluded. Only RECEIVED payouts count as cash.",
    }


# ----------------------------------------------------------------- personal capital
def capital_summary(deposits: list[dict], withdrawals: list[dict], recon: list[dict]) -> dict:
    dep = sum(num(d.get("amount"), 0.0) for d in deposits)
    wd = sum(num(w.get("amount"), 0.0) for w in withdrawals)
    dep_kes = sum(num(d.get("kes_equivalent"), 0.0) for d in deposits)
    wd_kes = sum(num(w.get("kes_equivalent"), 0.0) for w in withdrawals)
    charges = sum(num(x.get("charges"), 0.0) for x in deposits + withdrawals)
    estimated = sum(1 for x in deposits + withdrawals if (x.get("fx_rate_source") or "").upper() != "ACTUAL")
    out = {"deposits": round(dep, 2), "withdrawals": round(wd, 2), "net_deposits": round(dep - wd, 2),
           "deposits_kes": round(dep_kes, 2), "withdrawals_kes": round(wd_kes, 2),
           "transfer_charges": round(charges, 2), "transactions_with_estimated_fx": estimated}
    if recon:
        rows = sorted(recon, key=lambda r: r["period_end"])
        twr = 1.0
        for r in rows:
            opening = num(r.get("opening_equity"), 0.0)
            flow = num(r.get("deposits"), 0.0) - num(r.get("withdrawals"), 0.0)
            closing = num(r.get("closing_equity_statement"), None)
            if closing is None:
                continue
            base = opening + flow          # assumption: flows occur at start of period
            if base > 0:
                twr *= closing / base
        latest = rows[-1]
        current = num(latest.get("closing_equity_statement"), 0.0)
        first_open = num(rows[0].get("opening_equity"), 0.0)
        flows = sum(num(r.get("deposits"), 0.0) - num(r.get("withdrawals"), 0.0) for r in rows)
        out.update({
            "current_equity": round(current, 2),
            "trading_pl_excluding_deposits": round(current - first_open - flows, 2),
            "time_weighted_return_pct": round((twr - 1) * 100.0, 3),
            "unreconciled_periods": [r["period_end"] for r in rows if (r.get("reconciled") or "").upper() != "Y"],
            "twr_assumption": "Deposits and withdrawals treated as occurring at the start of each period.",
        })
    return out


def reconcile_row(r: dict) -> dict:
    """Compute closing equity from components; difference must be explained before 'reconciled = Y'."""
    computed = (num(r.get("opening_equity"), 0.0) + num(r.get("deposits"), 0.0) - num(r.get("withdrawals"), 0.0)
                + num(r.get("realised_pl"), 0.0) + num(r.get("unrealised_pl_change"), 0.0)
                - num(r.get("commissions"), 0.0) - num(r.get("swaps"), 0.0) - num(r.get("other_charges"), 0.0))
    stmt = num(r.get("closing_equity_statement"))
    return {"closing_equity_computed": round(computed, 2),
            "difference": None if stmt is None else round(stmt - computed, 2)}


# ----------------------------------------------------------------- dashboard
def _fmt(d: dict) -> str:
    return "\n".join(f"| {k} | {v} |" for k, v in d.items())


def build_dashboard(today: date | None = None) -> Path:
    today = today or date.today()
    trades = read_csv(JOURNAL)
    personal = [t for t in trades if (t.get("account_type") or "").upper() == "PERSONAL"]
    prop = [t for t in trades if (t.get("account_type") or "").upper() == "PROP"]
    paper = [t for t in trades if (t.get("account_type") or "").upper() in ("PAPER", "DEMO")]
    seal_status = verify_append_only() if JOURNAL.exists() else ["Journal missing"]
    lines = [f"# Performance Dashboard, {today.isoformat()}", "",
             "Generated by tos_analytics.py from the raw CSV records. Do not edit by hand.", "",
             "## Journal integrity", "", *(f"- {p}" for p in (seal_status or ["All sealed rows intact."])), "",
             "## Validation problems", "",
             *(f"- {p}" for p in (validate_trades(trades) or ["None found."])), ""]
    for title, subset in (("Personal account trades", personal), ("Prop firm account trades", prop),
                          ("Paper and demo trades (not live performance)", paper)):
        lines += [f"## {title}", "", "| Metric | Value |", "|---|---|", _fmt(trade_metrics(subset)), ""]
        if subset:
            lines += ["### By setup", ""]
            for k, m in breakdown(subset, "setup").items():
                lines.append(f"- {k}: n={m['n']}, expectancy_r={m.get('expectancy_r')}, PF={m.get('profit_factor')}")
            lines.append("")
    lines += ["## Rule compliance (all accounts)", "", "| Metric | Value |", "|---|---|", _fmt(compliance(trades)), "",
              "## Prop firm economics (real cash only)", "", "| Metric | Value |", "|---|---|",
              _fmt(prop_economics(read_csv(EVALS), read_csv(PAYOUTS))), "",
              "## Personal capital", "", "| Metric | Value |", "|---|---|",
              _fmt(capital_summary(read_csv(DEPOSITS), read_csv(WITHDRAWALS), read_csv(RECON))), ""]
    DASH_DIR.mkdir(parents=True, exist_ok=True)
    out = DASH_DIR / f"dashboard_{today.isoformat()}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main(argv: list[str]) -> None:
    cmd = argv[1] if len(argv) > 1 else "dashboard"
    if cmd == "dashboard":
        print(build_dashboard())
    elif cmd == "metrics":
        print(json.dumps(trade_metrics(read_csv(JOURNAL)), indent=2))
    elif cmd == "seal":
        print(f"Sealed {seal_journal()} rows")
    elif cmd == "verify":
        problems = verify_append_only()
        print("\n".join(problems) if problems else "All sealed rows intact.")
        sys.exit(1 if problems else 0)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
