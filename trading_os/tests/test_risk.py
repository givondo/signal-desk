"""Hand-worked checks for the position sizing and drawdown calculators.

Every expected number below was worked out by hand first (see comments), then
compared with the code. Inputs are illustrative test values, not trading records.
"""
import pytest

import Drawdown_Calculator as dc
import Position_Sizing as ps


# ---------------------------------------------------------------- position sizing
def test_xauusd_rounds_down_and_includes_costs():
    # budget = 10,000 x 0.5% = 50.00
    # adverse move = 7.50 stop + 0.30 spread + 0.10 slippage = 7.90; x 100 oz = 790 per lot
    # 50 / 790 = 0.0633 -> 0.06 lots; loss at stop = 0.06 x 790 = 47.40
    r = ps.size_position(equity=10_000, risk_pct=0.5, entry=2650.00, stop=2642.50,
                         spec=ps.DEFAULT_SPECS["XAUUSD"], spread=0.30, slippage=0.10)
    assert r.lots == 0.06
    assert r.loss_per_lot_at_stop == pytest.approx(790.0)
    assert r.expected_loss_at_stop == pytest.approx(47.40)
    assert r.expected_loss_at_stop <= r.risk_budget
    assert r.accepted


def test_stop_too_wide_returns_zero_lots():
    # budget = 1,000 x 0.5% = 5.00; one minimum lot (0.01) would lose 7.90
    r = ps.size_position(equity=1_000, risk_pct=0.5, entry=2650.00, stop=2642.50,
                         spec=ps.DEFAULT_SPECS["XAUUSD"], spread=0.30, slippage=0.10)
    assert r.lots == 0.0 and not r.accepted


def test_usdjpy_conversion_and_commission():
    # per lot = 0.50 x 100,000 / 150 = 333.33 + 7.00 commission = 340.33
    # 50 / 340.33 = 0.1469 -> 0.14 lots; loss = 47.65
    spec = ps.InstrumentSpec("USDJPY", 100_000, "JPY", commission_per_lot_round_turn=7.0)
    r = ps.size_position(equity=10_000, risk_pct=0.5, entry=150.00, stop=149.50, spec=spec,
                         conversion_rate=1 / 150.0)
    assert r.lots == 0.14
    assert r.expected_loss_at_stop == pytest.approx(47.65, abs=0.01)


def test_conversion_rate_required_when_currencies_differ():
    with pytest.raises(ValueError):
        ps.size_position(equity=10_000, risk_pct=0.5, entry=150.0, stop=149.5,
                         spec=ps.DEFAULT_SPECS["USDJPY"])


def test_risk_above_hard_cap_is_refused():
    with pytest.raises(ValueError, match="hard cap"):
        ps.size_position(equity=10_000, risk_pct=1.5, entry=2650, stop=2640,
                         spec=ps.DEFAULT_SPECS["XAUUSD"])


def test_missing_stop_is_refused():
    with pytest.raises(ValueError):
        ps.size_position(equity=10_000, risk_pct=0.5, entry=2650, stop=2650,
                         spec=ps.DEFAULT_SPECS["XAUUSD"])


def test_open_risk_and_correlation_checks():
    ok, _ = ps.check_open_risk([0.5], 0.5, max_aggregate_pct=1.0, max_trades=2)
    assert ok
    ok, msg = ps.check_open_risk([0.5, 0.5], 0.25, max_aggregate_pct=1.0, max_trades=2)
    assert not ok and "simultaneous" in msg
    limits = ps.load_limits()
    assert ps.correlated_group("XAUUSD", "BUY", limits["correlation_groups"]) == ["USD_SHORT_SIDE"]
    assert ps.correlated_group("EURUSD", "buy", limits["correlation_groups"]) == ["USD_SHORT_SIDE"]


# ---------------------------------------------------------------- drawdown
FTMO2 = dc.PRESETS["FTMO_2STEP"]


@pytest.mark.parametrize("equity,status", [
    (97_650, "OK"),         # daily used (98,900-97,650)/5,000 = 0.25
    (96_500, "WARN"),       # used 0.48 and internal stop 96,900 hit
    (95_000, "CRITICAL"),   # used 0.78
    (93_900, "BREACHED"),   # equals daily floor
])
def test_ftmo_two_step_status(equity, status):
    s = dc.AccountState(100_000, equity, 98_900)
    r = dc.evaluate(FTMO2, s)
    assert r.daily_floor == 93_900        # 98,900 - 5% x 100,000
    assert r.max_floor == 90_000          # static 10%
    assert r.binding_rule == "daily loss"
    assert r.internal_daily_stop == 96_900  # 98,900 - 0.4 x 5,000
    assert r.status == status


def test_ftmo_two_step_remaining_allowance():
    r = dc.evaluate(FTMO2, dc.AccountState(100_000, 97_650, 98_900))
    assert r.remaining_allowance == 3_750
    assert r.daily_used_fraction == pytest.approx(0.25)


def test_ftmo_one_step_trailing_floor_rises():
    # highest EOD 106,000 -> max floor 96,000; daily floor 105,000 - 3,000 = 102,000 binds
    r = dc.evaluate(dc.PRESETS["FTMO_1STEP"],
                    dc.AccountState(100_000, 104_000, 105_000, highest_eod_balance=106_000))
    assert r.max_floor == 96_000
    assert r.daily_floor == 102_000
    assert r.binding_rule == "daily loss"


def test_trailing_floor_locks_at_initial():
    # Topstep style: highest EOD 53,000 - 2,000 = 51,000, locked at 50,000
    s = dc.AccountState(50_000, 52_000, 52_000, highest_eod_balance=53_000)
    assert dc.max_floor(dc.PRESETS["TOPSTEP_50K_COMBINE"], s) == 50_000
    r = dc.evaluate(dc.PRESETS["TOPSTEP_50K_COMBINE"], s)
    assert any("not available" in w.lower() for w in r.warnings)


def test_replay_detects_max_loss_breach_on_day_three():
    days = [
        {"date": "D1", "min_equity": 97_000, "close_balance": 98_000},
        {"date": "D2", "min_equity": 93_500, "close_balance": 94_000},  # daily floor 93,000: survives
        {"date": "D3", "min_equity": 89_900, "close_balance": 90_500},  # daily floor 89,000, max 90,000
    ]
    out = dc.replay_days(FTMO2, 100_000, days)
    assert out == {"breached": True, "date": "D3", "rule": "maximum loss", "floor": 90_000}


def test_replay_clean_statement():
    days = [{"date": "D1", "min_equity": 99_500, "close_balance": 100_400}]
    assert dc.replay_days(FTMO2, 100_000, days) == {"breached": False, "final_balance": 100_400}


def test_simulator_bounds():
    always_win = dc.simulate_challenge(FTMO2, win_rate=0.999999, avg_win_r=2, avg_loss_r=1,
                                       risk_pct=0.5, paths=200)
    assert always_win["pass"] == 1.0
    losing = dc.simulate_challenge(FTMO2, win_rate=0.3, avg_win_r=1, avg_loss_r=1,
                                   risk_pct=1.0, paths=500)
    assert losing["pass"] < 0.05
    again = dc.simulate_challenge(FTMO2, win_rate=0.3, avg_win_r=1, avg_loss_r=1,
                                  risk_pct=1.0, paths=500)
    assert losing == again  # seeded and reproducible


def test_expected_value():
    # 30% pass, fee 540, first payout 2,000 net, fee refunded: 0.3 x 2,540 - 540 = 222
    assert dc.expected_value(0.3, 540, 2_000, True) == 222.0
    assert dc.expected_value(0.1, 540, 2_000, False) == -340.0
