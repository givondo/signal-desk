"""Hand-worked checks for journal metrics, prop economics, capital and the append-only seal.

All rows here are synthetic test inputs created inside the test. They are not trades.
"""
import csv

import pytest

import tos_analytics as ta


def _t(tid, pl, risk=50, ts="2026-01-0{}T10:00:00+00:00", **kw):
    row = {"trade_id": tid, "account_type": "PAPER", "instrument": "XAUUSD", "direction": "BUY",
           "entry_price": "1", "stop_loss": "0.5", "exit_price": "1", "initial_risk_amount": str(risk),
           "net_pl": str(pl), "exit_time_utc": ts.format(tid[-1]), "entry_time_utc": "", "setup": "TP"}
    row.update(kw)
    return row


def test_trade_metrics_hand_worked():
    # P/L 100, -50, -50, 150 with 50 risk each -> R 2, -1, -1, 3
    trades = [_t("T1", 100), _t("T2", -50), _t("T3", -50), _t("T4", 150)]
    m = ta.trade_metrics(trades)
    assert m["win_rate"] == 0.5
    assert m["avg_win"] == 125 and m["avg_loss"] == 50
    assert m["expectancy_per_trade"] == 37.5          # 0.5 x 125 - 0.5 x 50
    assert m["profit_factor"] == 2.5                  # 250 / 100
    assert m["expectancy_r"] == 0.75                  # (2 - 1 - 1 + 3) / 4
    assert m["max_consecutive_losses"] == 2
    assert m["max_drawdown"] == 100                   # peak 100 -> trough 0
    assert m["recovery_factor"] == 1.5                # 150 / 100
    assert m["verdict"].startswith("Insufficient sample")


def test_drawdown_percent_with_starting_equity():
    trades = [_t("T1", 100), _t("T2", -50), _t("T3", -50), _t("T4", 150)]
    m = ta.trade_metrics(trades, start_equity=1_000)
    assert m["max_drawdown_pct"] == pytest.approx(100 / 1_100 * 100, abs=0.01)


def test_validation_catches_duplicates_and_bad_r():
    trades = [_t("T1", 100, r_multiple="2"), _t("T1", -50), _t("T3", 60, r_multiple="3")]
    problems = ta.validate_trades(trades)
    assert any("duplicate" in p for p in problems)
    assert any("r_multiple 3" in p for p in problems)


def test_edge_verdict_needs_100_trades_and_positive_lower_bound():
    trades = [_t(f"T{i:03d}", 100 if i % 2 else -50, ts=f"2026-01-01T00:{i % 60:02d}:00+00:00")
              for i in range(120)]
    m = ta.trade_metrics(trades)
    assert m["n"] == 120
    assert m["verdict"].startswith("Credible edge")


def test_prop_economics_counts_only_received_payouts():
    evals = [{"fee_paid": "540", "fee_currency": "USD", "status": "FAILED", "purchase_type": "CHALLENGE"},
             {"fee_paid": "345", "fee_currency": "USD", "status": "FUNDED", "purchase_type": "CHALLENGE"}]
    payouts = [{"status": "RECEIVED", "amount_received": "800", "currency": "USD"},
               {"status": "REQUESTED", "amount_requested": "1000", "currency": "USD"},
               {"status": "DENIED", "amount_requested": "300", "currency": "USD"}]
    e = ta.prop_economics(evals, payouts)
    assert e["payouts_received_by_currency"] == {"USD": 800.0}
    assert e["payouts_pending_NOT_CASH"] == {"USD": 1000.0}
    assert e["net_usd_cash_result"] == -85.0          # 800 - 885
    assert e["payouts_denied"] == 1


def test_capital_twr_separates_deposits():
    recon = [{"period_end": "2026-01-31", "opening_equity": "0", "deposits": "1000", "withdrawals": "0",
              "closing_equity_statement": "1050", "reconciled": "Y"},
             {"period_end": "2026-02-28", "opening_equity": "1050", "deposits": "500", "withdrawals": "0",
              "closing_equity_statement": "1600", "reconciled": "N"}]
    deps = [{"amount": "1000", "kes_equivalent": "129000", "fx_rate_source": "ACTUAL"},
            {"amount": "500", "kes_equivalent": "64500", "fx_rate_source": "ESTIMATED"}]
    c = ta.capital_summary(deps, [], recon)
    # TWR = 1050/1000 x 1600/1550 - 1 = 8.387%
    assert c["time_weighted_return_pct"] == pytest.approx(8.387, abs=0.001)
    assert c["trading_pl_excluding_deposits"] == 100.0  # 1600 - 0 - 1500
    assert c["unreconciled_periods"] == ["2026-02-28"]
    assert c["transactions_with_estimated_fx"] == 1


def test_reconcile_row():
    r = ta.reconcile_row({"opening_equity": "1000", "deposits": "200", "withdrawals": "50",
                          "realised_pl": "75", "unrealised_pl_change": "-10", "commissions": "7",
                          "swaps": "3", "other_charges": "0", "closing_equity_statement": "1205"})
    assert r == {"closing_equity_computed": 1205.0, "difference": 0.0}


def test_seal_detects_edits_and_deletions(tmp_path):
    j = tmp_path / "j.csv"
    seal = tmp_path / "seal.json"
    rows = [["trade_id", "net_pl"], ["T1", "10"], ["T2", "-5"]]
    with open(j, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    assert ta.seal_journal(j, seal) == 2
    assert ta.verify_append_only(j, seal) == []
    with open(j, "a", newline="") as fh:            # appending is fine
        csv.writer(fh).writerow(["T3", "4"])
    assert ta.verify_append_only(j, seal) == []
    rows[2] = ["T2", "5"]                             # rewriting a loss as a win is caught
    with open(j, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    assert any("edited" in p for p in ta.verify_append_only(j, seal))


def test_dashboard_builds_on_empty_records(tmp_path, monkeypatch):
    monkeypatch.setattr(ta, "DASH_DIR", tmp_path)
    out = ta.build_dashboard()
    text = out.read_text()
    assert "No trades recorded yet." in text
    assert "Only RECEIVED payouts count as cash." in text
