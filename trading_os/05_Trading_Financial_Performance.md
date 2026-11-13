# 05 Trading Financial Performance

Created 13 November 2026. Current position: **no trades, deposits, fees or payouts recorded yet.** Every figure in this OS will come from the raw CSV files, calculated by `11_Performance_Analytics/tos_analytics.py`. Nothing here is estimated or typed in by hand.

## 1. Three ledgers that never mix

| Ledger | Files | What counts |
|---|---|---|
| Personal capital | `09_Personal_Capital/*.csv` | Your money: deposits, withdrawals, equity, realised and unrealised P/L, costs |
| Prop firm economics | `08_Prop_Firm_Accounts/*.csv` | Real cash only: fees and resets paid, refunds received, payouts **received** |
| Trading performance | `07_Trading_Journal/Trade_Journal.csv` | Every trade, labelled PERSONAL, PROP, DEMO or PAPER |

Nominal prop account sizes never appear in any total. Pending payouts are shown separately as "not cash".

## 2. Prop firm economics

| Measure | Definition |
|---|---|
| Total evaluation fees | Sum of `fee_paid` for CHALLENGE, RESET and ADDON purchases, by currency and in KES |
| Passed / failed / funded | Counts by `status` |
| Gross simulated profit | From `Payouts.csv` (`gross_simulated_profit`); informational only |
| Payouts requested | Status REQUESTED or APPROVED. **Not cash.** |
| Payouts received | Status RECEIVED, `amount_received` net of firm deductions |
| Effective payout ratio | Payouts received ÷ total fees paid |
| Net prop cash result | Payouts received + fee refunds − fees − payout transfer costs |
| Cost per funded account | Fees paid ÷ number of accounts that reached FUNDED |

A firm is "economically worthwhile" only when its net prop cash result is positive over at least two payout cycles.

## 3. Personal account economics

| Measure | Definition |
|---|---|
| Starting capital | `opening_capital` in `Accounts.csv` |
| Net deposits | Deposits − withdrawals |
| Trading P/L excluding deposits | Current equity − opening capital − net deposits |
| Trading costs | Commissions + swaps + other charges + transfer charges |
| Return on invested capital | Trading P/L ÷ (opening capital + net deposits) |
| Time-weighted return | Monthly sub-period returns chained, flows assumed at period start |
| Maximum drawdown | Largest equity fall from a peak, from month-end statements and the journal |

## 4. Consolidated dashboard

`python 11_Performance_Analytics/tos_analytics.py dashboard` writes `11_Performance_Analytics/Performance_Dashboard/dashboard_<date>.md` with:

- Journal integrity check (no sealed trade edited or deleted)
- Validation problems (missing stops, duplicate IDs, R multiples that do not match P/L)
- Personal, prop and paper/demo performance in separate tables, with breakdowns by setup
- Rule compliance rate and the P/L of trades that broke rules
- Prop economics (cash only) and personal capital (with trading returns separated from deposits)

Monthly and year-to-date views come from running the dashboard at each month end and saving the output into `Monthly_Reports/`.

## 5. Currency reporting

- Report in the transaction currency and in KES.
- `fx_rate_source = ACTUAL` when the rate is on a bank, M-Pesa or exchange receipt; `ESTIMATED` when taken from the Central Bank of Kenya indicative rate for that date. The dashboard counts estimated conversions.
- Never back-fill an actual rate with today's rate.

## 6. Monthly financial report template

Save as `11_Performance_Analytics/Monthly_Reports/YYYY-MM.md`.

```
# Monthly report YYYY-MM
Data status: all accounts reconciled? Y/N (list exceptions)

## Personal capital
Opening equity / deposits / withdrawals / trading P/L ex deposits / costs / closing equity
TWR month / TWR year to date / max drawdown to date

## Prop firms (cash only)
Fees paid this month / refunds / payouts received / pending (not cash)
Net prop cash result month and year to date
Account states: evaluations open, passed, failed, funded

## Trading performance (live only; paper and demo listed separately)
Trades / win rate / expectancy R and lower 95% bound / profit factor / max consecutive losses
By strategy and by session

## Total trading-related cash flow
Net realised trading income = personal realised P/L + payouts received − fees − costs

## Notes and anomalies
```

## 7. First-run checklist

1. Fill `09_Personal_Capital/Accounts.csv` with any existing account.
2. Import historical trades if any (question Q3 in the profile), labelled as imported in `lessons`.
3. Run `python 11_Performance_Analytics/tos_analytics.py seal` after each journaling session to lock rows.
4. Run the dashboard.
