# 03 Personal Capital Management

Created 13 November 2026. Personal capital is money you own and control. It is kept completely separate from prop firm nominal capital. A $100,000 funded account is not $100,000 of your money; only fees paid and payouts received are real cash.

## 1. Capital allocation waterfall

Money flows into trading only after every bucket above it is full. The order cannot be changed.

| Order | Bucket | Rule |
|---|---|---|
| 1 | Essential living expenses | Monthly obligations paid first, including family commitments. |
| 2 | Emergency savings | At least six months of essential expenses, held in a liquid, low-risk instrument (for example a CMA-approved money market fund). Never used for trading. |
| 3 | Precifarm operating capital | Ring-fenced. Company money is never used for personal trading or evaluation fees, and trading losses are never covered from it. |
| 4 | Long-term investments | Regular contributions continue regardless of trading results. |
| 5 | Personal trading capital | Money you can lose entirely without changing your life. Capped by the annual risk ceiling (Q8 in the profile). |
| 6 | Prop firm evaluation budget | A fixed quarterly amount inside the same ceiling. |

Hard rules:

- No borrowed money, credit cards, loans, mobile lending (M-Shwari, Fuliza, Tala and similar) or SACCO credit for trading or fees.
- No top-ups to cover losses. A loss is absorbed; the next deposit happens only on the scheduled date and only if the waterfall allows it.
- Annual risk ceiling = trading capital + evaluation budget for the next 12 months. Set once a year, recorded in `13_Decisions_and_Reviews/Trading_Decisions.md`.
- Evaluation budget: two consecutive failed evaluations send you back to demo for at least four weeks and a written review before another purchase.
- Profits withdrawn: once the personal account is up more than 10% on net deposits at a month end, withdraw at least half of the excess. Profits that never leave the broker are not yet profits.

## 2. Broker selection for personal capital

Use a broker licensed by the Capital Markets Authority (CMA) for money held in your own name. In May 2026 the CMA again warned the public against unlicensed online forex, crypto and money market schemes. Confirm a broker's licence on the CMA website before depositing; licences change. Brokers reported as CMA-licensed include Pepperstone Markets Kenya, EGM Securities, SCFM (Scope Markets), HF Markets, Exinity Capital East Africa, Windsor Markets Kenya and Exness KE. Verify each on the CMA register; this list is not an endorsement.

Record each account in `09_Personal_Capital/Accounts.csv`: broker, licence number, account type (standard or raw), base currency, platform, opening date, opening capital.

## 3. Account records

Every personal account is tracked with:

| Field | Where |
|---|---|
| Broker, account type, base currency | `Accounts.csv` |
| Opening capital | `Accounts.csv` |
| Deposits and withdrawals | `Deposits.csv`, `Withdrawals.csv` |
| Realised profit and loss, commissions, swaps, other charges | Monthly in `Account_Reconciliation.csv` from the broker statement |
| Unrealised P/L change | Monthly in `Account_Reconciliation.csv` |
| Spread cost where measurable | Trade journal (`gross_pl` vs theoretical mid-price result) or broker statement |
| Currency conversion | `fx_rate_to_kes` and `fx_rate_source` on every transaction |
| Current equity, used and free margin | Month-end statement; never balance alone |
| Maximum historical drawdown | `max_drawdown_pct_to_date` in the reconciliation file |

Balance excludes open positions; equity includes them. Every limit in this OS is measured on **equity**.

## 4. Deposits and withdrawals

Every transaction gets one row with: date, account, type, amount, currency, exchange rate, KES equivalent, rate source, charges, payment method (M-Pesa, bank transfer, card, Rise, stablecoin), reference and evidence.

- **Exchange rate source:** `ACTUAL` when the rate comes from your bank, M-Pesa or exchange receipt; `ESTIMATED` when taken from the Central Bank of Kenya indicative rate for that date. Estimated rates are labelled in every report.
- **Evidence:** save the M-Pesa message, bank advice, broker receipt or transaction hash in `09_Personal_Capital/evidence/` with the `txn_id` as file name.
- **M-Pesa:** record the M-Pesa transaction code as the reference and the transaction cost as `charges`.
- **Stablecoin routes:** record each leg separately (broker to wallet, wallet to exchange, exchange to M-Pesa), so conversion costs are visible.

## 5. Monthly reconciliation

On the first weekend of each month:

1. Download the broker statement for the previous month.
2. Fill one row per account in `Account_Reconciliation.csv`.
3. Run `python 11_Performance_Analytics/tos_analytics.py dashboard`. The `reconcile_row` function computes closing equity from components: opening + deposits − withdrawals + realised P/L + unrealised change − commissions − swaps − other charges.
4. Any difference above 1 USD must be explained in `notes` before `reconciled` is set to `Y`.
5. Unreconciled months are listed on the dashboard until closed.

Returns are reported two ways so deposits never masquerade as performance:

- **Trading P/L excluding deposits** = current equity − opening capital − net deposits.
- **Time-weighted return**, chained monthly, assuming flows at the start of each month.

## 6. Kenyan tax and foreign exchange notes

I am not a tax adviser; treat this as a briefing to take to one.

- Kenya has no separate tax category for forex trading; profits are generally assessed under ordinary income tax rules, and regular trading is likely to be treated as business income at individual rates rather than capital gains. The Finance Act 2025 tightened reporting expectations for traders.
- Prop firm payouts are paid for trading simulated accounts, so they look more like service or business income than investment gains. Treatment of fees as deductible expenses also needs advice.
- From 1 January 2027 individuals must file by 30 April (Finance Act 2026), and KRA cross-validates declared income against other data sources.
- Keep records for at least five years, the standard retention period under the Tax Procedures Act.
- Action: book a one-off consultation with a KRA-registered tax agent before the first payout or first profitable year. Record the advice in `Trading_Decisions.md`.

## 7. Open inputs

Annual risk ceiling (KES), emergency fund status, current trading balance, preferred broker. See questions Q7 to Q9 in `01_Trading_Profile_and_Objectives.md`.

## Sources

- [CMA warning on unlicensed forex, crypto and MMF schemes, May 2026](https://techtrendske.co.ke/2026/05/10/cma-warning-unlicensed-investment-schemes-kenya/)
- [Serrari Group: CMA-licensed online forex brokers](https://serrarigroup.com/cma-shares-list-of-licensed-online-trading-platforms-forex-dealers-in-kenya-amid-cbex-saga)
- [The Star: Finance Act 2025 and Kenyan forex traders](https://www.the-star.co.ke/news/2026-02-10-explainer-why-finance-act-2025-tax-rules-are-a-wake-up-call)
- [KRA: Finance Act 2026, what it means for you](https://www.kra.go.ke/finance-act-2026-what-it-means-for-you)
- [Cliffe Dekker Hofmeyr: Kenya tax outlook 2026](https://www.cliffedekkerhofmeyr.com/en/news/publications/2026/Kenya/Tax-Exchange-Control/tax-and-exchange-control-alert-27-january-Tax-outlook-2026-Navigating-Kenyas-evolving-tax-landscape)
