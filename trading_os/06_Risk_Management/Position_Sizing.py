"""Deterministic position sizing for GIVONDO Trading OS.

Rules this module enforces:
  * Size is always rounded DOWN to the broker volume step. It never rounds up.
  * Spread, slippage and commission are included in the loss at the stop, so the
    money lost if the stop is hit (with the assumed costs) stays within budget.
  * Risk above the hard cap in risk_limits.json is refused, not trimmed.
  * If the minimum volume would breach the risk budget, the answer is zero lots.
  * Missing or invalid inputs raise an error. Nothing is guessed.

Contract specifications differ between brokers and prop firms (XAUUSD contract
size is 100 oz on most MT5 servers but not all). Always confirm the symbol
specification in MT5 (right click the symbol, Specification) and record the
values you used in the journal.

Usage (command line):
  python Position_Sizing.py --equity 10000 --risk-pct 0.5 --symbol XAUUSD \
      --entry 2650.00 --stop 2642.50 --spread 0.30 --slippage 0.10
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

LIMITS_PATH = Path(__file__).with_name("risk_limits.json")


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    contract_size: float          # units of base asset per 1.00 lot
    quote_currency: str           # currency in which price is quoted
    volume_step: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 50.0
    commission_per_lot_round_turn: float = 0.0   # in ACCOUNT currency
    note: str = ""


# Typical MT5 values. VERIFY against your own server before use.
DEFAULT_SPECS: dict[str, InstrumentSpec] = {
    "XAUUSD": InstrumentSpec("XAUUSD", 100.0, "USD", note="100 oz per lot is typical; verify"),
    "EURUSD": InstrumentSpec("EURUSD", 100_000.0, "USD"),
    "GBPUSD": InstrumentSpec("GBPUSD", 100_000.0, "USD"),
    "USDJPY": InstrumentSpec("USDJPY", 100_000.0, "JPY"),
    "USDCHF": InstrumentSpec("USDCHF", 100_000.0, "CHF"),
    "USDCAD": InstrumentSpec("USDCAD", 100_000.0, "CAD"),
    "US100": InstrumentSpec("US100", 1.0, "USD", volume_step=0.1, volume_min=0.1,
                            note="Index CFD contract size varies widely by server; verify"),
}


@dataclass
class SizingResult:
    symbol: str
    lots: float
    risk_budget: float            # money allowed to lose at stop
    loss_per_lot_at_stop: float   # includes spread, slippage, commission
    expected_loss_at_stop: float  # lots * loss_per_lot_at_stop
    risk_pct_of_equity_actual: float
    stop_distance: float
    accepted: bool
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def load_limits(path: Path = LIMITS_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _floor_to_step(value: float, step: float) -> float:
    # Small epsilon guards against binary float artefacts such as 0.29999999.
    steps = math.floor(value / step + 1e-9)
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(steps * step, decimals)


def quote_to_account_rate(quote_currency: str, account_currency: str,
                          rate: float | None = None) -> float:
    """Units of account currency per 1 unit of quote currency.

    For XAUUSD on a USD account this is 1. For USDJPY on a USD account pass
    rate = 1 / USDJPY price. A rate is required whenever currencies differ.
    """
    if quote_currency == account_currency:
        return 1.0
    if rate is None or rate <= 0:
        raise ValueError(
            f"Conversion rate {quote_currency}->{account_currency} required and must be positive")
    return rate


def size_position(*, equity: float, risk_pct: float, entry: float, stop: float,
                  spec: InstrumentSpec, account_currency: str = "USD",
                  conversion_rate: float | None = None, spread: float = 0.0,
                  slippage: float = 0.0, hard_cap_pct: float | None = None,
                  account_kind: str = "personal") -> SizingResult:
    """Return the largest volume whose loss at the stop (with costs) fits the budget.

    equity: account equity (personal) or initial balance (prop, per policy).
    risk_pct: percent of equity, e.g. 0.5 means 0.5%.
    spread, slippage: in PRICE units (e.g. 0.30 for 30 cents on gold).
    """
    for name, val in (("equity", equity), ("entry", entry), ("stop", stop)):
        if val is None or not math.isfinite(val) or val <= 0:
            raise ValueError(f"{name} must be a positive number")
    if risk_pct is None or risk_pct <= 0:
        raise ValueError("risk_pct must be positive")
    if spread < 0 or slippage < 0:
        raise ValueError("spread and slippage cannot be negative")
    if entry == stop:
        raise ValueError("stop must differ from entry; every trade needs a stop-loss")

    if hard_cap_pct is None:
        limits = load_limits()
        key = "hard_cap_risk_per_trade_pct" if account_kind == "personal" else \
            "hard_cap_risk_per_trade_pct_of_initial"
        hard_cap_pct = limits[account_kind][key]
    if risk_pct > hard_cap_pct:
        raise ValueError(f"risk_pct {risk_pct}% exceeds hard cap {hard_cap_pct}%; refused")

    rate = quote_to_account_rate(spec.quote_currency, account_currency, conversion_rate)
    stop_distance = abs(entry - stop)
    adverse_move = stop_distance + spread + slippage
    loss_per_lot = adverse_move * spec.contract_size * rate + spec.commission_per_lot_round_turn
    budget = equity * risk_pct / 100.0

    raw_lots = budget / loss_per_lot
    lots = min(_floor_to_step(raw_lots, spec.volume_step), spec.volume_max)
    reasons: list[str] = []
    accepted = True
    if lots < spec.volume_min:
        accepted = False
        lots = 0.0
        reasons.append("Minimum volume would exceed the risk budget: stop too wide or account too small. Skip the trade.")
    if raw_lots > spec.volume_max:
        reasons.append("Capped at maximum volume for the symbol.")
    expected_loss = lots * loss_per_lot
    if expected_loss > budget + 1e-9:  # defensive: should be impossible
        raise AssertionError("sizing exceeded budget")
    return SizingResult(
        symbol=spec.symbol, lots=lots, risk_budget=round(budget, 2),
        loss_per_lot_at_stop=round(loss_per_lot, 4),
        expected_loss_at_stop=round(expected_loss, 2),
        risk_pct_of_equity_actual=round(expected_loss / equity * 100.0, 4),
        stop_distance=round(stop_distance, 10), accepted=accepted, reasons=reasons)


def check_open_risk(open_risks_pct: list[float], new_risk_pct: float,
                    max_aggregate_pct: float, max_trades: int) -> tuple[bool, str]:
    """Fail-closed check of aggregate open risk before adding a trade."""
    if len(open_risks_pct) + 1 > max_trades:
        return False, f"Would exceed {max_trades} simultaneous trades"
    total = sum(open_risks_pct) + new_risk_pct
    if total > max_aggregate_pct + 1e-9:
        return False, f"Aggregate open risk {total:.2f}% exceeds {max_aggregate_pct}%"
    return True, f"Aggregate open risk {total:.2f}% within {max_aggregate_pct}%"


def correlated_group(symbol: str, direction: str, groups: dict[str, list[str]]) -> list[str]:
    tag = f"{symbol.upper()}:{direction.upper()}"
    return [g for g, members in groups.items() if tag in members]


def main() -> None:
    p = argparse.ArgumentParser(description="Position size calculator (rounds down, includes costs)")
    p.add_argument("--equity", type=float, required=True)
    p.add_argument("--risk-pct", type=float, required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--entry", type=float, required=True)
    p.add_argument("--stop", type=float, required=True)
    p.add_argument("--spread", type=float, default=0.0)
    p.add_argument("--slippage", type=float, default=0.0)
    p.add_argument("--commission", type=float, default=None, help="per lot round turn, account currency")
    p.add_argument("--contract-size", type=float, default=None)
    p.add_argument("--account-currency", default="USD")
    p.add_argument("--rate", type=float, default=None, help="quote to account currency rate")
    p.add_argument("--kind", choices=["personal", "prop"], default="personal")
    a = p.parse_args()
    base = DEFAULT_SPECS.get(a.symbol.upper())
    if base is None and a.contract_size is None:
        raise SystemExit("Unknown symbol: pass --contract-size")
    spec = InstrumentSpec(
        symbol=a.symbol.upper(),
        contract_size=a.contract_size or base.contract_size,
        quote_currency=base.quote_currency if base else a.account_currency,
        volume_step=base.volume_step if base else 0.01,
        volume_min=base.volume_min if base else 0.01,
        commission_per_lot_round_turn=a.commission if a.commission is not None else 0.0)
    r = size_position(equity=a.equity, risk_pct=a.risk_pct, entry=a.entry, stop=a.stop,
                      spec=spec, account_currency=a.account_currency, conversion_rate=a.rate,
                      spread=a.spread, slippage=a.slippage, account_kind=a.kind)
    print(json.dumps(r.as_dict(), indent=2))


if __name__ == "__main__":
    main()
