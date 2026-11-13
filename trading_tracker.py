"""Trading tracker for Signal Desk: the GIVONDO Trading OS records, served by the desk.

Stores trades, prop firm evaluations, account snapshots, payouts, deposits,
withdrawals and psychology entries as append-only CSV files under
DATA_DIR/trading_os_data (the same folder layout as trading_os/). Column
headers come from the templates in trading_os/, so the tracker and the
Trading OS never drift apart.

Integrity rules:
  * Rows are appended, never edited. Corrections go in the corrections file.
  * Before every write the trade journal is checked against its seal; if any
    sealed row was changed outside the tracker, writes are refused.
  * Only RECEIVED payouts count as cash. Nominal prop balances are never
    added to personal capital.
  * Calculations are deterministic (trading_os calculators); nothing here
    places, modifies or closes an order.

Stdlib only.
"""
from __future__ import annotations

import csv
import io
import os
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OS_DIR = HERE / "trading_os"
for _sub in ("06_Risk_Management", "11_Performance_Analytics"):
    _p = str(OS_DIR / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import Drawdown_Calculator as dc  # noqa: E402
import Position_Sizing as ps  # noqa: E402
import tos_analytics as ta  # noqa: E402

# kind -> (relative path, id column or None)
KINDS: dict[str, tuple[str, str | None]] = {
    "trades": ("07_Trading_Journal/Trade_Journal.csv", "trade_id"),
    "corrections": ("07_Trading_Journal/Trade_Corrections.csv", "correction_id"),
    "missed": ("07_Trading_Journal/Missed_Trades.csv", "missed_id"),
    "violations": ("07_Trading_Journal/Rule_Violations.csv", "violation_id"),
    "psychology": ("07_Trading_Journal/Psychology_Log.csv", None),
    "evaluations": ("08_Prop_Firm_Accounts/Evaluations.csv", "record_id"),
    "snapshots": ("08_Prop_Firm_Accounts/Account_Snapshots.csv", "snapshot_id"),
    "funded": ("08_Prop_Firm_Accounts/Funded_Accounts.csv", None),
    "payouts": ("08_Prop_Firm_Accounts/Payouts.csv", "payout_id"),
    "accounts": ("09_Personal_Capital/Accounts.csv", None),
    "deposits": ("09_Personal_Capital/Deposits.csv", "txn_id"),
    "withdrawals": ("09_Personal_Capital/Withdrawals.csv", "txn_id"),
    "reconciliation": ("09_Personal_Capital/Account_Reconciliation.csv", None),
    "networth": ("09_Personal_Capital/Net_Worth.csv", "entry_id"),
    "fills": ("07_Trading_Journal/Broker_Fills.csv", "fill_id"),
    "fill_decisions": ("07_Trading_Journal/Fill_Decisions.csv", "decision_id"),
}

REQUIRED: dict[str, tuple[str, ...]] = {
    "trades": ("account_type", "instrument", "direction", "entry_price", "stop_loss",
               "exit_price", "initial_risk_amount", "net_pl", "rule_compliance"),
    "corrections": ("trade_id", "field", "corrected_value", "reason"),
    "missed": ("instrument", "reason_missed"),
    "violations": ("rule_broken", "description"),
    "psychology": ("date",),
    "evaluations": ("account_label", "firm", "programme", "purchase_type", "nominal_size",
                    "purchase_date", "fee_paid", "fee_currency", "status"),
    "snapshots": ("account_label", "rules_preset", "stage", "initial_balance", "equity",
                  "day_start_balance"),
    "funded": ("account_label", "firm", "funded_date"),
    "payouts": ("account_label", "firm", "requested_date", "amount_requested", "currency", "status"),
    "accounts": ("account", "broker", "base_currency"),
    "deposits": ("date", "account", "amount", "currency", "fx_rate_source"),
    "withdrawals": ("date", "account", "amount", "currency", "fx_rate_source"),
    "reconciliation": ("period_end", "account", "opening_equity", "closing_equity_statement"),
    "networth": ("date", "item", "category", "amount", "currency"),
    "fills": ("source", "broker", "broker_trade_id", "instrument", "direction"),
    "fill_decisions": ("fill_id", "decision"),
}

ASSET_CATEGORIES = ("CASH", "EMERGENCY_FUND", "INVESTMENTS", "TRADING_ACCOUNT", "BUSINESS_EQUITY",
                    "PROPERTY", "VEHICLE", "RECEIVABLE", "OTHER_ASSET")
LIABILITY_CATEGORIES = ("LOAN", "CREDIT_CARD", "MOBILE_LOAN", "OTHER_LIABILITY")

NUMERIC = {
    "entry_price", "stop_loss", "take_profit", "exit_price", "volume", "initial_risk_amount",
    "gross_pl", "commission", "swap", "net_pl", "r_multiple", "nominal_size", "fee_paid",
    "fx_rate_to_kes", "fee_kes", "starting_balance", "initial_balance", "equity",
    "day_start_balance", "highest_eod_balance", "amount", "kes_equivalent", "charges",
    "amount_requested", "amount_received", "gross_simulated_profit", "profit_split_pct",
    "payout_fees", "kes_received", "opening_equity", "closing_equity_statement", "deposits",
    "withdrawals", "realised_pl", "unrealised_pl_change", "commissions", "swaps", "other_charges",
    "fee_refund_received", "planned_entry", "planned_stop", "planned_target", "hypothetical_r",
    "cost_amount", "opening_capital", "sleep_hours",
}

ENUMS = {
    "account_type": {"PERSONAL", "PROP", "DEMO", "PAPER"},
    "direction": {"BUY", "SELL"},
    "rule_compliance": {"FOLLOWED", "VIOLATED"},
    "fx_rate_source": {"ACTUAL", "ESTIMATED"},
    "status_payouts": {"REQUESTED", "APPROVED", "RECEIVED", "DENIED"},
    "status_evaluations": {"DISCOVERY", "SELECTED", "PURCHASED", "EVALUATION", "PASSED", "FAILED",
                           "FUNDED", "PAYOUT ELIGIBLE", "PAYOUT RECEIVED", "SCALED", "CLOSED"},
}

PAPER_LABEL = "SIGNAL-DESK-PAPER"
MAX_FIELD = 2000
_lock = threading.Lock()


class TrackerError(ValueError):
    """Bad input or a refused write; the message is safe to show the user."""


def data_root() -> Path:
    base = os.environ.get("TOS_DATA_DIR")
    if base:
        return Path(base)
    return Path(os.environ.get("DATA_DIR", str(HERE))) / "trading_os_data"


def _path(kind: str) -> Path:
    if kind not in KINDS:
        raise TrackerError(f"unknown record type: {kind}")
    return data_root() / KINDS[kind][0]


def _template_header(kind: str) -> list[str]:
    if kind not in KINDS:
        raise TrackerError(f"unknown record type: {kind}")
    with open(OS_DIR / KINDS[kind][0], newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh))


def ensure_files() -> Path:
    """Create the data folder and any missing CSV with its template header."""
    root = data_root()
    for kind in KINDS:
        p = _path(kind)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(_template_header(kind))
    seal = root / "07_Trading_Journal" / ".journal_seal.json"
    if not seal.exists():
        ta.seal_journal(_path("trades"), seal)
    return root


def _seal_path() -> Path:
    return data_root() / "07_Trading_Journal" / ".journal_seal.json"


def integrity_problems() -> list[str]:
    ensure_files()
    return ta.verify_append_only(_path("trades"), _seal_path())


def read(kind: str) -> list[dict]:
    ensure_files()
    return ta.read_csv(_path(kind))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(kind: str, existing: set[str]) -> str:
    prefix = {"trades": "T", "corrections": "C", "missed": "M", "violations": "V",
              "evaluations": "E", "snapshots": "S", "payouts": "P", "deposits": "D",
              "withdrawals": "W", "networth": "NW", "fill_decisions": "FD"}.get(kind, "R")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    n = 1
    while f"{prefix}-{stamp}-{n}" in existing:
        n += 1
    return f"{prefix}-{stamp}-{n}"


def _clean(value) -> str:
    s = "" if value is None else str(value)
    s = s.replace("\r", " ").replace("\n", " ").strip()
    if len(s) > MAX_FIELD:
        raise TrackerError("field too long")
    return s


def _validate(kind: str, row: dict, existing: list[dict]) -> None:
    missing = [f for f in REQUIRED.get(kind, ()) if not row.get(f)]
    if missing:
        raise TrackerError("missing: " + ", ".join(missing))
    for k, v in row.items():
        if k in NUMERIC and v != "":
            try:
                float(v.replace(",", ""))
            except ValueError:
                raise TrackerError(f"{k} must be a number") from None
    for field in ("account_type", "direction", "rule_compliance", "fx_rate_source"):
        if row.get(field) and row[field].upper() not in ENUMS[field]:
            raise TrackerError(f"{field} must be one of {sorted(ENUMS[field])}")
        if row.get(field):
            row[field] = row[field].upper()
    status_key = f"status_{kind}"
    if status_key in ENUMS and row.get("status"):
        row["status"] = row["status"].upper()
        if row["status"] not in ENUMS[status_key]:
            raise TrackerError(f"status must be one of {sorted(ENUMS[status_key])}")
    if kind == "trades":
        if float(row["initial_risk_amount"]) <= 0:
            raise TrackerError("initial_risk_amount must be positive: no stop, no trade")
        if row.get("rule_compliance") == "VIOLATED" and not row.get("violation_detail"):
            raise TrackerError("violation_detail is required when a rule was violated")
        problems = ta.validate_trades(existing + [row])
        dup = [p for p in problems if "duplicate" in p]
        if dup:
            raise TrackerError(dup[-1])
    if kind == "snapshots" and row["rules_preset"] not in dc.PRESETS:
        raise TrackerError(f"rules_preset must be one of {sorted(dc.PRESETS)}")
    if kind == "fill_decisions" and row["decision"].upper() not in ("IGNORED", "RESTORED"):
        raise TrackerError("decision must be IGNORED or RESTORED")
    if kind == "fill_decisions":
        row["decision"] = row["decision"].upper()
    if kind == "networth":
        row["category"] = row["category"].upper().replace(" ", "_")
        if row["category"] in ASSET_CATEGORIES:
            row["type"] = "ASSET"
        elif row["category"] in LIABILITY_CATEGORIES:
            row["type"] = "LIABILITY"
        else:
            raise TrackerError("category must be one of " + ", ".join(ASSET_CATEGORIES + LIABILITY_CATEGORIES))
        if float(row["amount"]) < 0:
            raise TrackerError("enter liabilities as positive amounts; the category makes them negative")
        row["currency"] = row["currency"].upper()
        if row["currency"] != "KES" and not row.get("fx_rate_to_kes"):
            raise TrackerError("fx_rate_to_kes is required for non-KES items")
        row["liquid"] = (row.get("liquid") or "N").upper()[:1]
    if kind == "payouts" and row.get("status") == "RECEIVED" and not row.get("amount_received"):
        raise TrackerError("amount_received is required for a RECEIVED payout")


def append(kind: str, row: dict) -> dict:
    """Validate and append one row. Returns the stored row."""
    if not isinstance(row, dict):
        raise TrackerError("row must be an object")
    with _lock:
        ensure_files()
        header = _template_header(kind)
        unknown = sorted(set(row) - set(header))
        if unknown:
            raise TrackerError("unknown fields: " + ", ".join(unknown))
        clean = {h: _clean(row.get(h)) for h in header}
        existing = ta.read_csv(_path(kind))
        id_col = KINDS[kind][1]
        if id_col and not clean.get(id_col):
            clean[id_col] = _new_id(kind, {r.get(id_col, "") for r in existing})
        for stamp_col in ("recorded_at_utc",):
            if stamp_col in clean and not clean[stamp_col]:
                clean[stamp_col] = _now_iso()
        if kind == "trades":
            _derive_trade_fields(clean)
        _validate(kind, clean, existing)
        if kind == "trades":
            problems = ta.verify_append_only(_path("trades"), _seal_path())
            if problems:
                raise TrackerError("journal integrity check failed, writes refused: " + problems[0])
        with open(_path(kind), "a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow([clean[h] for h in header])
        if kind == "trades":
            ta.seal_journal(_path("trades"), _seal_path())
        return clean


def _derive_trade_fields(row: dict) -> None:
    def f(k):
        return float(row[k]) if row.get(k) not in (None, "") else None
    if not row.get("net_pl") and row.get("gross_pl"):
        row["net_pl"] = f"{f('gross_pl') - (f('commission') or 0) - (f('swap') or 0):.2f}"
    if not row.get("r_multiple") and row.get("net_pl") and f("initial_risk_amount"):
        row["r_multiple"] = f"{f('net_pl') / f('initial_risk_amount'):.2f}"


# ----------------------------------------------------------------- paper sync
def sync_paper(resolved_by_symbol: dict[str, list[dict]]) -> int:
    """Copy closed Signal Desk paper tickets into the journal as PAPER trades (in R units).

    Idempotent: a ticket already journaled is skipped. The engine keeps only its
    last 500 tickets per instrument; the journal keeps them for good.
    """
    added = 0
    existing = {r.get("trade_id") for r in read("trades")}
    for sym, tickets in sorted(resolved_by_symbol.items()):
        for p in sorted(tickets, key=lambda t: t.get("closed", 0)):
            tid = f"PAPER-{sym}-{p.get('id')}-{p.get('opened')}"
            if tid in existing or "r" not in p or not p.get("closed"):
                continue
            row = {
                "trade_id": tid, "account_type": "PAPER", "account_label": PAPER_LABEL,
                "firm_or_broker": "Signal Desk paper engine", "instrument": sym,
                "direction": "BUY" if p.get("dir") == "LONG" else "SELL",
                "setup": p.get("model") or "", "strategy_id": "SIGNAL-DESK-ENGINE",
                "timeframe": "MTF", "session": "",
                "entry_time_utc": _epoch_iso(p.get("filled", p.get("opened"))),
                "exit_time_utc": _epoch_iso(p.get("closed")),
                "entry_price": p.get("entry"), "stop_loss": p.get("sl"), "take_profit": p.get("tp1"),
                "exit_price": p.get("exit"), "volume": "", "initial_risk_amount": 1,
                "currency": "R", "gross_pl": p.get("gross_r"), "commission": p.get("cost_r"),
                "swap": 0, "net_pl": p.get("r"), "r_multiple": p.get("r"),
                "market_conditions": f"grade {p.get('grade', '-')}, confidence {p.get('conf', '-')}",
                "rule_compliance": "FOLLOWED",
                "lessons": f"engine outcome {p.get('outcome')}; MFE {p.get('mfe_r')}R, MAE {p.get('mae_r')}R",
            }
            try:
                append("trades", row)
                existing.add(tid)
                added += 1
            except TrackerError:
                continue
    return added


def _epoch_iso(v) -> str:
    try:
        return datetime.fromtimestamp(int(v), timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return ""


# ----------------------------------------------------------------- broker fills
FILL_NUMERIC = ("volume", "entry_price", "exit_price", "stop_loss", "take_profit", "gross_pl", "commission", "swap", "net_pl")


def _slug(v: str) -> str:
    return "".join(ch for ch in (v or "").upper() if ch.isalnum() or ch in "-_")[:40] or "X"


def fill_key(source: str, broker: str, broker_trade_id: str) -> str:
    """Deterministic ID shared by the fill and the journal trade created from it."""
    return f"{_slug(source)[:4]}-{_slug(broker)}-{_slug(broker_trade_id)}"


def import_fills(source: str, broker: str, account_label: str, account_type: str, fills: list) -> dict:
    """Store closed trades read from a broker or TradingView. Idempotent by broker trade id.

    Fills land in an inbox first. A fill becomes a journal trade only when its stop
    and money at risk are known (sent by the sync, or added by you in the tracker),
    so no trade enters the journal without a defined risk.
    """
    if not isinstance(fills, list):
        raise TrackerError("fills must be a list")
    account_type = (account_type or "PERSONAL").upper()
    if account_type not in ENUMS["account_type"]:
        raise TrackerError("account_type must be one of " + ", ".join(sorted(ENUMS["account_type"])))
    if len(fills) > 2000:
        raise TrackerError("too many fills in one request (max 2000)")
    existing = {r.get("fill_id") for r in read("fills")}
    added, dupes, journaled, errors = 0, 0, 0, []
    for f in fills:
        if not isinstance(f, dict):
            errors.append("skipped a fill that is not an object")
            continue
        tid = str(f.get("broker_trade_id") or "").strip()
        if not tid:
            errors.append("skipped a fill without broker_trade_id")
            continue
        fid = fill_key(source, broker, tid)
        if fid in existing:
            dupes += 1
            continue
        side = str(f.get("direction") or "").upper()
        side = {"LONG": "BUY", "SHORT": "SELL"}.get(side, side)
        row = {"fill_id": fid, "source": source, "broker": broker, "account_label": account_label,
               "account_type": account_type, "broker_trade_id": tid, "instrument": str(f.get("instrument") or "").upper(),
               "direction": side, "open_time_utc": f.get("open_time_utc") or "", "close_time_utc": f.get("close_time_utc") or "",
               "currency": f.get("currency") or "USD", "raw": str(f.get("raw") or "")[:600]}
        for k in FILL_NUMERIC:
            row[k] = f.get(k, "")
        try:
            append("fills", row)
        except TrackerError as e:
            errors.append(f"{tid}: {e}")
            continue
        existing.add(fid)
        added += 1
        risk = ta.num(f.get("initial_risk_amount"))
        if risk and risk > 0 and row.get("stop_loss") not in ("", None):
            try:
                journal_fill(fid, {"initial_risk_amount": risk, "stop_loss": row["stop_loss"],
                                   "rule_compliance": "FOLLOWED", "lessons": "auto-journaled by sync"})
                journaled += 1
            except TrackerError as e:
                errors.append(f"{tid}: not journaled: {e}")
    return {"added": added, "duplicates": dupes, "auto_journaled": journaled, "errors": errors[:20]}


def pending_fills() -> list[dict]:
    """Fills not yet in the journal and not ignored, newest first."""
    journal_ids = {r.get("trade_id") for r in read("trades")}
    state: dict[str, str] = {}
    for d in read("fill_decisions"):
        state[d.get("fill_id")] = d.get("decision")
    out = [f for f in read("fills") if f["fill_id"] not in journal_ids and state.get(f["fill_id"]) != "IGNORED"]
    return sorted(out, key=lambda f: f.get("close_time_utc") or f.get("recorded_at_utc") or "", reverse=True)


def journal_fill(fill_id: str, extra: dict) -> dict:
    """Turn one fill into a journal trade, adding the stop, risk and review fields you supply."""
    fill = next((f for f in read("fills") if f.get("fill_id") == fill_id), None)
    if fill is None:
        raise TrackerError("unknown fill")
    if fill_id in {r.get("trade_id") for r in read("trades")}:
        raise TrackerError("this fill is already in the journal")
    extra = extra or {}
    allowed = {"stop_loss", "take_profit", "initial_risk_amount", "setup", "strategy_id", "timeframe", "session",
               "rule_compliance", "violation_detail", "emotional_state", "lessons", "market_conditions",
               "screenshot_before", "screenshot_after", "account_type"}
    unknown = sorted(set(extra) - allowed)
    if unknown:
        raise TrackerError("unknown fields: " + ", ".join(unknown))
    net = fill.get("net_pl")
    if net in ("", None) and fill.get("gross_pl") not in ("", None):
        net = f"{float(fill['gross_pl']) - (ta.num(fill.get('commission')) or 0) - (ta.num(fill.get('swap')) or 0):.2f}"
    row = {"trade_id": fill_id, "account_type": extra.get("account_type") or fill.get("account_type") or "PERSONAL",
           "account_label": fill.get("account_label"), "firm_or_broker": f"{fill.get('broker')} via {fill.get('source')}",
           "instrument": fill.get("instrument"), "direction": fill.get("direction"),
           "entry_time_utc": fill.get("open_time_utc"), "exit_time_utc": fill.get("close_time_utc"),
           "entry_price": fill.get("entry_price"), "exit_price": fill.get("exit_price"), "volume": fill.get("volume"),
           "take_profit": extra.get("take_profit") or fill.get("take_profit"),
           "stop_loss": extra.get("stop_loss") or fill.get("stop_loss"),
           "initial_risk_amount": extra.get("initial_risk_amount"), "currency": fill.get("currency"),
           "gross_pl": fill.get("gross_pl"), "commission": fill.get("commission"), "swap": fill.get("swap"),
           "net_pl": net, "rule_compliance": extra.get("rule_compliance") or "FOLLOWED"}
    for k in ("setup", "strategy_id", "timeframe", "session", "violation_detail", "emotional_state", "lessons",
              "market_conditions", "screenshot_before", "screenshot_after"):
        if extra.get(k):
            row[k] = extra[k]
    return append("trades", {k: v for k, v in row.items() if v not in (None,)})


# ----------------------------------------------------------------- summary
def prop_status() -> list[dict]:
    """Latest snapshot per prop account, run through the firm's drawdown rules."""
    latest: dict[str, dict] = {}
    for s in read("snapshots"):
        latest[s["account_label"]] = s
    out = []
    for label, s in sorted(latest.items()):
        rules = dc.PRESETS.get(s.get("rules_preset", ""))
        entry = {"account_label": label, "firm": s.get("firm"), "stage": s.get("stage"),
                 "as_of": s.get("recorded_at_utc"), "preset": s.get("rules_preset")}
        if rules is None:
            entry["error"] = "unknown rules preset"
        else:
            st = dc.AccountState(
                initial_balance=ta.num(s.get("initial_balance")), equity=ta.num(s.get("equity")),
                day_start_balance=ta.num(s.get("day_start_balance")),
                highest_eod_balance=ta.num(s.get("highest_eod_balance")))
            rep = dc.evaluate(rules, st)
            entry.update(rep.__dict__)
            entry["rules_verified_on"] = rules.verified_on
        out.append(entry)
    return out


def _latest_fx() -> dict[str, tuple[float, str, str]]:
    """Most recent known KES rate per currency: (rate, source, date)."""
    rates: dict[str, tuple[float, str, str]] = {"KES": (1.0, "ACTUAL", "")}
    pools = [(r, r.get("date") or "") for r in read("networth") + read("deposits") + read("withdrawals")]
    pools += [(r, r.get("received_date") or r.get("requested_date") or "") for r in read("payouts")]
    for r, d in sorted(pools, key=lambda x: x[1]):
        ccy = (r.get("currency") or "").upper()
        rate = ta.num(r.get("fx_rate_to_kes"))
        if ccy and rate:
            rates[ccy] = (rate, (r.get("fx_rate_source") or "ESTIMATED").upper(), d)
    return rates


def net_worth() -> dict:
    """Personal net worth in KES: latest value of each item, plus broker equity from statements.

    Prop firm nominal balances are never included. Liabilities reduce net worth.
    """
    latest: dict[str, dict] = {}
    for i, r in enumerate(read("networth")):
        key = (r.get("item") or "").strip().casefold()
        if key:
            prev = latest.get(key)
            if prev is None or (r.get("date") or "", i) >= (prev.get("date") or "", prev["_i"]):
                latest[key] = dict(r, _i=i)
    rates = _latest_fx()
    warnings: list[str] = []
    lines = []
    for key, r in latest.items():
        amount = ta.num(r.get("amount"), 0.0)
        if amount == 0:
            continue
        rate = ta.num(r.get("fx_rate_to_kes")) or rates.get((r.get("currency") or "").upper(), (None,))[0]
        lines.append({"date": r.get("date"), "item": r.get("item"), "category": r.get("category"),
                      "type": r.get("type"), "liquid": r.get("liquid") or "N", "amount": amount,
                      "currency": r.get("currency"), "fx_rate_to_kes": rate,
                      "fx_rate_source": r.get("fx_rate_source") or ("ACTUAL" if r.get("currency") == "KES" else ""),
                      "source": "Manual entry", "notes": r.get("notes") or ""})
    recon_latest: dict[str, dict] = {}
    for r in sorted(read("reconciliation"), key=lambda x: x.get("period_end") or ""):
        recon_latest[(r.get("account") or "").strip().casefold()] = r
    for key, r in recon_latest.items():
        if not key or key in latest:
            continue
        ccy = (r.get("currency") or "USD").upper()
        fx = rates.get(ccy)
        if fx is None:
            warnings.append(f"No KES rate known for {ccy}: broker account {r.get('account')} left out. Add a net worth item or a deposit with an exchange rate.")
            continue
        lines.append({"date": r.get("period_end"), "item": r.get("account"), "category": "TRADING_ACCOUNT",
                      "type": "ASSET", "liquid": "Y", "amount": ta.num(r.get("closing_equity_statement"), 0.0),
                      "currency": ccy, "fx_rate_to_kes": fx[0], "fx_rate_source": "ESTIMATED" if fx[1] != "ACTUAL" else "ACTUAL",
                      "source": f"Broker statement {r.get('period_end')}", "notes": f"Rate from {fx[2] or 'latest record'}"})
    for ln in lines:
        sign = -1 if ln["type"] == "LIABILITY" else 1
        ln["kes_value"] = round(sign * ln["amount"] * (ln["fx_rate_to_kes"] or 0), 2)
    lines.sort(key=lambda x: (x["type"] != "ASSET", x["category"] or "", x["item"] or ""))
    assets = sum(x["kes_value"] for x in lines if x["type"] == "ASSET")
    liabilities = -sum(x["kes_value"] for x in lines if x["type"] == "LIABILITY")
    liquid = sum(x["kes_value"] for x in lines if x["type"] == "ASSET" and x["liquid"] == "Y") - liabilities
    by_cat: dict[str, float] = {}
    for x in lines:
        by_cat[x["category"]] = round(by_cat.get(x["category"], 0.0) + x["kes_value"], 2)
    nw = assets - liabilities
    trading = by_cat.get("TRADING_ACCOUNT", 0.0)
    return {"lines": lines, "assets_kes": round(assets, 2), "liabilities_kes": round(liabilities, 2),
            "net_worth_kes": round(nw, 2), "liquid_net_worth_kes": round(liquid, 2), "by_category": by_cat,
            "trading_share_of_net_worth": round(trading / nw, 4) if nw > 0 else None,
            "warnings": warnings, "estimated_fx_lines": sum(1 for x in lines if x["fx_rate_source"] == "ESTIMATED"),
            "note": "Prop firm nominal balances are excluded. Liabilities are subtracted."}


def summary() -> dict:
    ensure_files()
    trades = read("trades")
    by_type = {k: [t for t in trades if (t.get("account_type") or "").upper() in v]
               for k, v in {"personal": {"PERSONAL"}, "prop": {"PROP"}, "practice": {"PAPER", "DEMO"}}.items()}
    section = {}
    for k, rows in by_type.items():
        section[k] = {"metrics": ta.trade_metrics(rows), "by_setup": ta.breakdown(rows, "setup") if rows else {},
                      "by_session": ta.breakdown(rows, "session") if rows else {},
                      "by_instrument": ta.breakdown(rows, "instrument") if rows else {}}
    return {
        "generated_at": _now_iso(),
        "integrity": integrity_problems(),
        "validation": ta.validate_trades(trades),
        "trades": section,
        "compliance": ta.compliance([t for t in trades if t.get("account_type") != "PAPER"]),
        "prop_economics": ta.prop_economics(read("evaluations"), read("payouts")),
        "prop_accounts": prop_status(),
        "capital": ta.capital_summary(read("deposits"), read("withdrawals"), read("reconciliation")),
        "net_worth": net_worth(),
        "pending_fills": len(pending_fills()),
        "counts": {k: len(read(k)) for k in KINDS},
        "presets": {k: {"firm": r.firm, "programme": r.programme, "verified_on": r.verified_on,
                        "kenya_eligible": r.kenya_eligible} for k, r in dc.PRESETS.items()},
        "limits": ps.load_limits(),
    }


def records(kind: str, limit: int = 200) -> dict:
    rows = read(kind)
    return {"kind": kind, "header": _template_header(kind), "total": len(rows),
            "rows": list(reversed(rows))[: max(1, min(limit, 2000))]}


def export_csv(kind: str) -> bytes:
    """CSV download with spreadsheet formula injection neutralised."""
    rows = read(kind)
    header = _template_header(kind)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_safe_cell(r.get(h, "")) for h in header])
    return buf.getvalue().encode("utf-8")


def _safe_cell(v: str) -> str:
    v = v or ""
    if v[:1] in ("=", "+", "-", "@") and not _is_number(v):
        return "'" + v
    return v


def _is_number(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def backup_zip() -> bytes:
    root = ensure_files()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(root).as_posix())
    return buf.getvalue()


# ----------------------------------------------------------------- calculators
def size(body: dict) -> dict:
    sym = (body.get("symbol") or "XAUUSD").upper()
    base = ps.DEFAULT_SPECS.get(sym)
    contract = _f(body, "contract_size") or (base.contract_size if base else None)
    if contract is None:
        raise TrackerError("unknown symbol: give contract_size")
    spec = ps.InstrumentSpec(
        symbol=sym, contract_size=contract,
        quote_currency=(body.get("quote_currency") or (base.quote_currency if base else "USD")).upper(),
        volume_step=base.volume_step if base else 0.01, volume_min=base.volume_min if base else 0.01,
        commission_per_lot_round_turn=_f(body, "commission") or 0.0)
    kind = "prop" if body.get("kind") == "prop" else "personal"
    try:
        r = ps.size_position(equity=_f(body, "equity"), risk_pct=_f(body, "risk_pct"),
                             entry=_f(body, "entry"), stop=_f(body, "stop"), spec=spec,
                             account_currency=(body.get("account_currency") or "USD").upper(),
                             conversion_rate=_f(body, "rate"), spread=_f(body, "spread") or 0.0,
                             slippage=_f(body, "slippage") or 0.0, account_kind=kind)
    except ValueError as e:
        raise TrackerError(str(e)) from None
    return r.as_dict()


def drawdown(body: dict) -> dict:
    rules = dc.PRESETS.get(body.get("preset", ""))
    if rules is None:
        raise TrackerError(f"preset must be one of {sorted(dc.PRESETS)}")
    try:
        st = dc.AccountState(_f(body, "initial"), _f(body, "equity"), _f(body, "day_start_balance"),
                             highest_eod_balance=_f(body, "highest_eod_balance"))
        return dc.evaluate(rules, st).__dict__ | {"rules_verified_on": rules.verified_on,
                                                  "source": rules.source, "notes": rules.notes}
    except (TypeError, ValueError) as e:
        raise TrackerError(str(e) or "initial, equity and day_start_balance are required") from None


def _f(body: dict, key: str) -> float | None:
    v = body.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise TrackerError(f"{key} must be a number") from None


def handle(method: str, path: str, query: dict, body: dict | None, paper_source=None):
    """Route a tracker API call. Returns (status, content_type, bytes, extra_headers)."""
    import json
    js = "application/json"
    try:
        if method == "GET" and path == "/api/tracker/summary":
            if paper_source is not None:
                sync_paper(paper_source())
            return 200, js, json.dumps(summary()).encode(), {}
        if method == "GET" and path == "/api/tracker/records":
            kind = (query.get("kind") or ["trades"])[0]
            limit = int((query.get("limit") or ["200"])[0])
            return 200, js, json.dumps(records(kind, limit)).encode(), {}
        if method == "GET" and path == "/api/tracker/export":
            kind = (query.get("kind") or ["trades"])[0]
            name = Path(KINDS[kind][0]).name if kind in KINDS else "export.csv"
            return 200, "text/csv; charset=utf-8", export_csv(kind), {
                "Content-Disposition": f'attachment; filename="{name}"'}
        if method == "GET" and path == "/api/tracker/workbook.xlsx":
            import tracker_workbook
            stamp = time.strftime("%Y-%m-%d")
            return 200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", tracker_workbook.build(), {
                "Content-Disposition": f'attachment; filename="Trading_Tracker_{stamp}.xlsx"'}
        if method == "GET" and path == "/api/tracker/backup":
            stamp = time.strftime("%Y%m%d-%H%M")
            return 200, "application/zip", backup_zip(), {
                "Content-Disposition": f'attachment; filename="trading_os_data_{stamp}.zip"'}
        if method == "POST" and path == "/api/tracker/append":
            body = body or {}
            row = append(body.get("kind", ""), body.get("row") or {})
            return 200, js, json.dumps({"ok": True, "row": row}).encode(), {}
        if method == "POST" and path == "/api/tracker/size":
            return 200, js, json.dumps({"ok": True, "result": size(body or {})}).encode(), {}
        if method == "POST" and path == "/api/tracker/drawdown":
            return 200, js, json.dumps({"ok": True, "result": drawdown(body or {})}).encode(), {}
        if method == "GET" and path == "/api/tracker/fills/pending":
            return 200, js, json.dumps({"ok": True, "fills": pending_fills()}).encode(), {}
        if method == "POST" and path == "/api/tracker/import/fills":
            b = body or {}
            res = import_fills(str(b.get("source") or "tradingview"), str(b.get("broker") or "unknown"),
                               str(b.get("account_label") or ""), str(b.get("account_type") or "PERSONAL"), b.get("fills") or [])
            return 200, js, json.dumps({"ok": True, **res}).encode(), {}
        if method == "POST" and path == "/api/tracker/fills/journal":
            b = body or {}
            row = journal_fill(str(b.get("fill_id") or ""), b.get("fields") or {})
            return 200, js, json.dumps({"ok": True, "row": row}).encode(), {}
        if method == "POST" and path == "/api/tracker/fills/ignore":
            b = body or {}
            if not any(f.get("fill_id") == b.get("fill_id") for f in read("fills")):
                raise TrackerError("unknown fill")
            row = append("fill_decisions", {"fill_id": b.get("fill_id"), "decision": "IGNORED", "reason": b.get("reason") or ""})
            return 200, js, json.dumps({"ok": True, "row": row}).encode(), {}
        if method == "POST" and path == "/api/tracker/sync_paper":
            n = sync_paper(paper_source()) if paper_source else 0
            return 200, js, json.dumps({"ok": True, "added": n}).encode(), {}
        return 404, js, b'{"ok": false, "error": "not found"}', {}
    except TrackerError as e:
        return 400, js, json.dumps({"ok": False, "error": str(e)}).encode(), {}
    except (KeyError, ValueError) as e:
        return 400, js, json.dumps({"ok": False, "error": f"bad request: {e}"}).encode(), {}
