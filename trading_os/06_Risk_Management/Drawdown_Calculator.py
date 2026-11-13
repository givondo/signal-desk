"""Deterministic drawdown and loss-limit calculator for prop and personal accounts.

The firm's own methodology always wins. Each preset records where its rules
came from and the date they were read. If a preset says reference_verified =
False, the daily-loss reference point was not confirmed in official documents:
read the firm's current rules page before relying on the number.

What it answers:
  * Where are today's daily-loss floor and the maximum-loss floor?
  * How much money is left before each floor, and which floor binds first?
  * Where is my own internal stop (tighter than the firm's)?
  * Status: OK / WARN / CRITICAL / BREACHED.

It also includes a simple Monte Carlo challenge simulator (closed-trade
approximation, see simulate_challenge) for estimating pass probability and
expected value before paying a fee. It is a planning aid, not a forecast.

Usage:
  python Drawdown_Calculator.py --preset FTMO_2STEP --initial 100000 \
      --equity 97650 --day-start-balance 98900
  python Drawdown_Calculator.py --list
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, asdict
from pathlib import Path

LIMITS_PATH = Path(__file__).with_name("risk_limits.json")


@dataclass(frozen=True)
class FirmRules:
    key: str
    firm: str
    programme: str
    daily_loss_pct: float | None          # e.g. 5.0
    daily_amount_basis: str               # "initial" (pct of initial) or "reference" (pct of reference)
    daily_reference: str                  # "day_start_balance" | "max_day_start_balance_equity"
    daily_includes_floating: bool
    max_loss_pct: float | None            # percent of initial
    max_loss_amount: float | None         # fixed money amount (futures style), overrides pct
    max_loss_mode: str                    # "static" | "trailing_eod" | "trailing_intraday"
    trailing_locks_at_initial: bool
    profit_targets_pct: tuple[float, ...]
    min_trading_days: int
    best_day_max_share_of_profit: float | None   # e.g. 0.5
    kenya_eligible: bool | None
    reference_verified: bool
    source: str
    verified_on: str
    notes: str = ""


PRESETS: dict[str, FirmRules] = {
    "FTMO_2STEP": FirmRules(
        "FTMO_2STEP", "FTMO", "2-Step Challenge (Standard)", 5.0, "initial", "day_start_balance", True,
        10.0, None, "static", False, (10.0, 5.0), 4, None, True, True,
        "ftmo.com/en/trading-objectives (page modified 2 Nov 2026)", "2026-11-13",
        "Daily limit resets 00:00 CE(S)T from that day's opening balance. Floating P/L, swaps and commissions count."),
    "FTMO_1STEP": FirmRules(
        "FTMO_1STEP", "FTMO", "1-Step Challenge", 3.0, "initial", "day_start_balance", True,
        10.0, None, "trailing_eod", False, (10.0,), 0, 0.5, True, True,
        "ftmo.com/en/trading-objectives (page modified 2 Nov 2026)", "2026-11-13",
        "Max loss trails the highest 00:00 CE(S)T balance (or initial if higher) and only rises. Best day <= 50% of total profit for orders from 12 Oct 2026. Fee not refundable."),
    "FUNDEDNEXT_STELLAR_2STEP": FirmRules(
        "FUNDEDNEXT_STELLAR_2STEP", "FundedNext", "Stellar 2-Step", 5.0, "initial", "day_start_balance", True,
        10.0, None, "static", False, (8.0, 5.0), 5, None, None, False,
        "fundednext.com/cfd-challenge-terms (updated 5 May 2026); static max loss per fundedfast.com read 8 Nov 2026",
        "2026-11-13",
        "Daily-loss reference point not confirmed in the terms page. Confirm whether it is day-start balance or the higher of balance and equity."),
    "FUNDEDNEXT_STELLAR_1STEP": FirmRules(
        "FUNDEDNEXT_STELLAR_1STEP", "FundedNext", "Stellar 1-Step", 3.0, "initial", "day_start_balance", True,
        6.0, None, "static", False, (10.0,), 2, None, None, False,
        "fundednext.com/cfd-challenge-terms (updated 5 May 2026)", "2026-11-13",
        "Static vs trailing max loss not stated on the terms page. Verify before purchase."),
    "THE5ERS_HIGH_STAKES": FirmRules(
        "THE5ERS_HIGH_STAKES", "The5ers", "High Stakes (New)", 5.0, "initial", "day_start_balance", True,
        10.0, None, "static", False, (10.0, 5.0), 0, None, None, False,
        "the5ers.com/high-stakes (read 13 Nov 2026)", "2026-11-13",
        "3 profitable days (>=0.5% of initial each) required per step. No order placement 2 minutes either side of high-impact news. Classic option uses 8% then 5%."),
    "ALPHA_PRO_10": FirmRules(
        "ALPHA_PRO_10", "Alpha Capital Group", "Alpha Pro 10%", 5.0, "initial", "day_start_balance", True,
        10.0, None, "static", False, (10.0, 5.0), 3, None, None, True,
        "alphacapitalgroup.uk (read 13 Nov 2026); targets per fundedfast.com read 9 Nov 2026", "2026-11-13",
        "Daily drawdown from balance at 00:00 GMT+2; floating P/L counts. Weekend holding not allowed on the qualified account."),
    "TOPSTEP_50K_COMBINE": FirmRules(
        "TOPSTEP_50K_COMBINE", "Topstep", "Trading Combine 50K", None, "initial", "day_start_balance", True,
        None, 2000.0, "trailing_eod", True, (6.0,), 2, 0.5, False, False,
        "help.topstep.com eligibility FAQ (Kenya listed as ineligible)", "2026-11-13",
        "NOT AVAILABLE: Kenyan citizens are ineligible to trade or receive payouts. Kept only for reference. Sources disagree on intraday vs end-of-day trailing."),
}


@dataclass
class AccountState:
    initial_balance: float
    equity: float                       # current equity including floating P/L
    day_start_balance: float
    day_start_equity: float | None = None
    highest_eod_balance: float | None = None
    highest_equity: float | None = None


@dataclass
class DrawdownReport:
    preset: str
    equity: float
    daily_floor: float | None
    max_floor: float
    binding_floor: float
    binding_rule: str
    remaining_daily: float | None
    remaining_max: float
    remaining_allowance: float
    daily_used_fraction: float | None
    max_used_fraction: float
    internal_daily_stop: float | None
    internal_stop_hit: bool
    status: str
    distance_to_failure_pct_of_initial: float
    warnings: list


def _daily_reference(rules: FirmRules, s: AccountState) -> float:
    if rules.daily_reference == "max_day_start_balance_equity":
        dse = s.day_start_equity if s.day_start_equity is not None else s.day_start_balance
        return max(s.day_start_balance, dse)
    return s.day_start_balance


def daily_floor(rules: FirmRules, s: AccountState) -> float | None:
    if rules.daily_loss_pct is None:
        return None
    ref = _daily_reference(rules, s)
    base = s.initial_balance if rules.daily_amount_basis == "initial" else ref
    return ref - rules.daily_loss_pct / 100.0 * base


def max_loss_amount(rules: FirmRules, initial: float) -> float:
    if rules.max_loss_amount is not None:
        return rules.max_loss_amount
    if rules.max_loss_pct is None:
        raise ValueError("preset has no maximum loss defined")
    return rules.max_loss_pct / 100.0 * initial


def max_floor(rules: FirmRules, s: AccountState) -> float:
    amount = max_loss_amount(rules, s.initial_balance)
    if rules.max_loss_mode == "static":
        return s.initial_balance - amount
    if rules.max_loss_mode == "trailing_eod":
        peak = max(s.highest_eod_balance or s.initial_balance, s.initial_balance)
    elif rules.max_loss_mode == "trailing_intraday":
        peak = max(s.highest_equity or s.initial_balance, s.initial_balance)
    else:
        raise ValueError(f"unknown max_loss_mode {rules.max_loss_mode}")
    floor = peak - amount
    if rules.trailing_locks_at_initial:
        floor = min(floor, s.initial_balance)
    return floor


def evaluate(rules: FirmRules, s: AccountState, limits: dict | None = None) -> DrawdownReport:
    if s.initial_balance <= 0 or s.equity is None or s.day_start_balance is None:
        raise ValueError("initial_balance, equity and day_start_balance are required")
    limits = limits or json.loads(LIMITS_PATH.read_text(encoding="utf-8"))
    warn_f = limits["alerts"]["warn_when_allowance_used_fraction"]
    crit_f = limits["alerts"]["critical_when_allowance_used_fraction"]
    internal_frac = limits["prop"]["internal_daily_stop_fraction_of_firm_daily"]

    d_floor = daily_floor(rules, s)
    m_floor = max_floor(rules, s)
    candidates = [(m_floor, "maximum loss")]
    if d_floor is not None:
        candidates.append((d_floor, "daily loss"))
    binding_floor, binding_rule = max(candidates, key=lambda c: c[0])

    warnings: list[str] = []
    if not rules.reference_verified:
        warnings.append("Daily-loss reference not verified in official documents. Confirm on the firm's site.")
    if rules.kenya_eligible is False:
        warnings.append("This programme is not available to Kenyan traders.")

    remaining_max = s.equity - m_floor
    max_span = max(_peak_for_max(rules, s) - m_floor, 1e-9)
    max_used = min(max(1 - remaining_max / max_span, 0.0), 1.0) if remaining_max > 0 else 1.0

    remaining_daily = daily_used = internal_stop = None
    internal_hit = False
    if d_floor is not None:
        ref = _daily_reference(rules, s)
        remaining_daily = s.equity - d_floor
        span = max(ref - d_floor, 1e-9)
        daily_used = min(max(1 - remaining_daily / span, 0.0), 1.0) if remaining_daily > 0 else 1.0
        internal_stop = ref - internal_frac * span
        internal_hit = s.equity <= internal_stop

    used = max(max_used, daily_used or 0.0)
    if s.equity <= binding_floor:
        status = "BREACHED"
    elif used >= crit_f:
        status = "CRITICAL"
    elif used >= warn_f or internal_hit:
        status = "WARN"
    else:
        status = "OK"
    if internal_hit and status != "BREACHED":
        warnings.append("Internal daily stop reached: stop trading this account for the day.")

    remaining = s.equity - binding_floor
    return DrawdownReport(
        preset=rules.key, equity=round(s.equity, 2),
        daily_floor=None if d_floor is None else round(d_floor, 2),
        max_floor=round(m_floor, 2), binding_floor=round(binding_floor, 2), binding_rule=binding_rule,
        remaining_daily=None if remaining_daily is None else round(remaining_daily, 2),
        remaining_max=round(remaining_max, 2), remaining_allowance=round(remaining, 2),
        daily_used_fraction=None if daily_used is None else round(daily_used, 4),
        max_used_fraction=round(max_used, 4),
        internal_daily_stop=None if internal_stop is None else round(internal_stop, 2),
        internal_stop_hit=internal_hit, status=status,
        distance_to_failure_pct_of_initial=round(remaining / s.initial_balance * 100.0, 4),
        warnings=warnings)


def _peak_for_max(rules: FirmRules, s: AccountState) -> float:
    if rules.max_loss_mode == "static":
        return s.initial_balance
    if rules.max_loss_mode == "trailing_eod":
        return max(s.highest_eod_balance or s.initial_balance, s.initial_balance)
    return max(s.highest_equity or s.initial_balance, s.initial_balance)


def replay_days(rules: FirmRules, initial: float, days: list[dict]) -> dict:
    """Replay a statement day by day.

    days: list of {"date", "min_equity", "close_balance"} where min_equity is the
    lowest intraday equity (from the platform or statement) and close_balance is
    the balance at the firm's daily reset. Returns the first breach, if any.
    """
    day_start = initial
    highest_eod = initial
    for d in days:
        s = AccountState(initial, d["min_equity"], day_start, highest_eod_balance=highest_eod,
                         highest_equity=max(highest_eod, d.get("max_equity", day_start)))
        dfl = daily_floor(rules, s)
        mfl = max_floor(rules, s)
        if dfl is not None and d["min_equity"] <= dfl:
            return {"breached": True, "date": d["date"], "rule": "daily loss", "floor": round(dfl, 2)}
        if d["min_equity"] <= mfl:
            return {"breached": True, "date": d["date"], "rule": "maximum loss", "floor": round(mfl, 2)}
        day_start = d["close_balance"]
        highest_eod = max(highest_eod, day_start)
    return {"breached": False, "final_balance": day_start}


def simulate_challenge(rules: FirmRules, *, win_rate: float, avg_win_r: float, avg_loss_r: float,
                       risk_pct: float, trades_per_day: int = 1, max_days: int = 120,
                       paths: int = 5000, seed: int = 42) -> dict:
    """Monte Carlo pass/fail estimate on CLOSED trades.

    Simplifications (all make the result optimistic): no floating drawdown inside
    a trade, no slippage beyond what avg_loss_r already contains, independent
    trades, fixed risk. Treat the output as an upper bound.
    """
    if not (0 < win_rate < 1):
        raise ValueError("win_rate must be between 0 and 1")
    rng = random.Random(seed)
    initial = 100_000.0
    risk = risk_pct / 100.0 * initial
    outcomes = {"pass": 0, "fail_daily": 0, "fail_max": 0, "timeout": 0}
    for _ in range(paths):
        balance = initial
        highest_eod = initial
        phase = 0
        phase_start = initial
        phase_days = 0
        day_profits: list[float] = []
        result = "timeout"
        for _day in range(max_days):
            day_start = balance
            s = AccountState(initial, balance, day_start, highest_eod_balance=highest_eod)
            dfl = daily_floor(rules, s)
            mfl = max_floor(rules, s)
            for _t in range(trades_per_day):
                balance += risk * avg_win_r if rng.random() < win_rate else -risk * avg_loss_r
                if dfl is not None and balance <= dfl:
                    result = "fail_daily"
                    break
                if balance <= mfl:
                    result = "fail_max"
                    break
            if result != "timeout":
                break
            phase_days += 1
            day_profits.append(balance - day_start)
            highest_eod = max(highest_eod, balance)
            target = rules.profit_targets_pct[phase] / 100.0 * initial
            consistency_ok = True
            total = balance - phase_start
            if rules.best_day_max_share_of_profit and total > 0:
                consistency_ok = max(day_profits) <= rules.best_day_max_share_of_profit * total
            if total >= target and phase_days >= rules.min_trading_days and consistency_ok:
                phase += 1
                if phase == len(rules.profit_targets_pct):
                    result = "pass"
                    break
                balance = initial          # new phase account resets
                phase_start = initial
                highest_eod = initial
                phase_days = 0
                day_profits = []
        outcomes[result] += 1
    return {k: round(v / paths, 4) for k, v in outcomes.items()} | {
        "paths": paths, "assumptions": "closed trades only, independent outcomes, fixed risk; optimistic"}


def expected_value(pass_probability: float, fee: float, expected_first_payout: float,
                   fee_refunded_on_payout: bool) -> float:
    """Expected cash result of ONE attempt, in fee currency. Payout must already be net of split."""
    gain = expected_first_payout + (fee if fee_refunded_on_payout else 0.0)
    return round(pass_probability * gain - fee, 2)


def main() -> None:
    p = argparse.ArgumentParser(description="Prop firm drawdown calculator")
    p.add_argument("--list", action="store_true")
    p.add_argument("--preset")
    p.add_argument("--initial", type=float)
    p.add_argument("--equity", type=float)
    p.add_argument("--day-start-balance", type=float)
    p.add_argument("--day-start-equity", type=float)
    p.add_argument("--highest-eod-balance", type=float)
    a = p.parse_args()
    if a.list or not a.preset:
        for k, r in PRESETS.items():
            print(f"{k:28s} {r.firm} {r.programme} (verified {r.verified_on})")
        return
    rules = PRESETS[a.preset]
    s = AccountState(a.initial, a.equity, a.day_start_balance, a.day_start_equity, a.highest_eod_balance)
    print(json.dumps(asdict(evaluate(rules, s)), indent=2))


if __name__ == "__main__":
    main()
