# 04 Trading Strategies and Research

Created 13 November 2026. Every trading idea here is a **hypothesis** until it passes the validation gate in section 4. Popular concepts (liquidity sweeps, supply and demand, Fibonacci, RSI divergence) are tested, never assumed to work.

## 1. Market selection

Chosen to fit your experience (gold-focused), your time (overlap session, limited weekday hours) and your access (CFD prop firms; futures prop firms unavailable from Kenya).

| Market | Decision | Reason |
|---|---|---|
| XAUUSD | **Primary** | Your existing focus; deep liquidity in the overlap; strong, readable macro drivers (USD, real yields, Fed). Wide spreads and violent news spikes make cost and news rules essential. |
| EURUSD | Optional second, after 50 journaled XAUUSD trades | Tightest costs; a clean read on USD; partly correlated with gold, so open risk is shared under the USD correlation group. |
| DXY, US 10-year yield, US100 | Watch only | Context for gold and risk sentiment. Not traded. |
| Futures | Excluded | Topstep and FTMO Futures both exclude Kenya. |
| Crypto | Excluded for now | Weekend gaps, high CFD spreads at prop firms, and a second skill set. Revisit after 12 months. |
| Other commodities and indices | Excluded for now | Focus beats breadth while the edge is unproven. |

## 2. Session map (East Africa Time)

| Period | Northern winter (Nov to Mar) | Northern summer |
|---|---|---|
| London open | 11:00 | 10:00 |
| New York FX activity begins | 16:00 | 15:00 |
| US data releases (08:30 ET) | 16:30 | 15:30 |
| US cash equity open (09:30 ET) | 17:30 | 16:30 |
| London close | 20:00 | 19:00 |
| **Overlap window to trade** | **16:00 to 20:00** | **15:00 to 19:00** |
| FOMC statement (14:00 ET) | 22:00 | 21:00 |

UK clocks change on the last Sunday of March and October; US clocks on the second Sunday of March and first Sunday of November. For the weeks between, the overlap shifts by an hour. Check `10_Market_Intelligence/Economic_Calendar.md`.

## 3. Strategy pipeline

```
IDEA → HYPOTHESIS (written spec) → BACKTEST with costs → WALK-FORWARD out-of-sample
     → PAPER / DEMO forward test → SMALL LIVE or prop evaluation → FULL SIZE
```

Each step has a gate. A strategy can move back a step at any time. It never skips one.

Testing runs in Signal Desk (`givondo/signal-desk`), which already has a cost-aware backtester, walk-forward validation and a random-entry baseline. This OS holds the specifications and the results; Signal Desk does the computing. Raw backtest outputs go in `11_Performance_Analytics/Strategy_Analytics/<strategy_id>/` with the code commit hash.

## 4. Validation gate (all must pass before live capital)

| Test | Threshold |
|---|---|
| Data | Real market data from a named source, at least 2 years, gaps checked |
| Costs | Spread, slippage and commission included at realistic or worse-than-measured levels |
| Sample | At least 100 trades in-sample and 50 out-of-sample |
| Out-of-sample expectancy | At least +0.15R per trade after costs |
| Out-of-sample profit factor | At least 1.3 after costs |
| Robustness | Still positive when each parameter is moved ±20%; no single month supplies more than 30% of profit |
| Baseline | Beats the random-entry baseline with the same exits and costs |
| Drawdown fit | 95th percentile simulated drawdown below 50% of the target prop firm's maximum loss at the planned risk |
| Forward test | At least 30 demo trades with expectancy within one standard error of the backtest |

Rules that protect the test:

- Parameters are frozen before out-of-sample testing. Once a test has been seen, changing parameters creates a **new** strategy version, recorded in the strategy's change log, and the test starts again.
- No look-ahead: signals use only completed candles.
- Backtest results are never presented as live performance. Every report labels BACKTEST, PAPER, DEMO or LIVE.

## 5. Strategy register

| ID | Name | Status | Evidence so far |
|---|---|---|---|
| S-001 | XAUUSD trend pullback (overlap) | HYPOTHESIS: spec written below | Coded in Signal Desk; no edge on synthetic data; untested on real data |
| S-002 | XAUUSD pre-New York range breakout | HYPOTHESIS: spec below | Session breakout coded in Signal Desk; untested on real data |
| S-003 | Breakout and retest | IDEA | Coded in Signal Desk; untested |
| S-004 | Momentum continuation | IDEA | Coded in Signal Desk; untested |
| S-005 | Mean reversion | IDEA | Coded in Signal Desk; untested; usually weak on trending gold |
| S-006 | Multi-timeframe trend | IDEA | Coded in Signal Desk; overlaps S-001 |

Priority: test S-001 and S-002 first. They fit the overlap window and need no overnight holding.

The parameters below are written as testable starting points. Align them with the Signal Desk implementation before running, and log any differences.

### S-001 XAUUSD trend pullback

| Field | Specification |
|---|---|
| Market and instrument | Spot gold CFD, XAUUSD |
| Timeframes | H1 for trend, M15 for entry |
| Session | Entries only inside the overlap window (16:00 to 20:00 EAT in winter) |
| Market condition | H1 close above EMA 50 and EMA 50 rising over the last 5 bars, and ADX(14) on H1 above 20 (mirror for shorts) |
| Entry criteria | Price pulls back on M15 to touch or close within 0.25 × ATR(14, M15) of EMA 20 on M15 |
| Confirmation | An M15 candle closes back in the trend direction beyond the previous candle's high (long) or low (short). Entry at the next candle open |
| Stop-loss | Beyond the pullback swing extreme plus 0.25 × ATR(14, M15) |
| Take-profit | Fixed 2R. No partials during testing, so results stay measurable |
| Exit conditions | Target, stop, or time stop at 8 hours, or close before 23:00 EAT if still open |
| Position sizing | `Position_Sizing.py`, 0.5% of equity (personal) or of initial balance (prop), costs included |
| Risk/reward assumption | 2R target; breakeven win rate about 34% before costs |
| Invalidation | H1 closes back through EMA 50, or ADX falls below 15 before entry |
| News restriction | No new entry from 15 minutes before to 15 minutes after a tier-1 USD release; no entries on FOMC day after 20:00 EAT |
| Maximum attempts | 2 per day |
| Known weaknesses | Choppy ranges produce repeated small losses; spread widening near news; gold gaps at the weekly open |
| Backtest results | Pending: real data not yet tested |
| Forward test results | Pending |

### S-002 XAUUSD pre-New York range breakout

| Field | Specification |
|---|---|
| Timeframes | M15 for range, M5 for execution |
| Range | High and low from 13:00 to 16:00 EAT (winter), London morning |
| Market condition | Range height between 0.6 and 1.6 × ATR(14, H1); skip days outside this band |
| Entry criteria | M5 candle closes beyond the range boundary between 16:00 and 18:00 EAT |
| Confirmation | Next M5 candle does not close back inside the range. Entry at that candle's close |
| Stop-loss | Midpoint of the range |
| Take-profit | 1.5R, or the range height projected from the breakout level, whichever is closer |
| Exit conditions | Target, stop, or close at 20:00 EAT |
| News restriction | Skip the day if a tier-1 USD release falls inside 16:15 to 16:45 EAT and price has not yet broken out; never enter in the minute of a release |
| Maximum attempts | 1 per day (no re-entry in the opposite direction) |
| Known weaknesses | False breakouts on data days; range definition sensitive to DST changes |
| Backtest results | Pending |
| Forward test results | Pending |

## 6. Research agenda (concepts to test, in order)

1. Does an H1 trend filter improve M15 pullback expectancy on gold versus no filter? (S-001 vs ablation)
2. Do US data days (CPI, NFP, FOMC) need to be excluded, or do they carry the edge?
3. Is ATR-based stop distance better than structure-based?
4. Does the London morning range predict New York direction on gold (S-002)?
5. Liquidity-sweep entries (sweep of the prior day's high or low then reversal): defined as a mechanical rule, then tested against random entries.
6. Do DXY or US 10-year yield moves in the prior hour improve gold entries?

Each answer is recorded in `13_Decisions_and_Reviews/Trading_Decisions.md` with the test ID and result.

## 7. Quantitative definitions

- Expectancy: E = (win rate × average win) − (loss rate × average loss). Reported in money and in R.
- Profit factor: gross profits ÷ gross losses.
- Risk amount: account equity × risk percentage.
- R multiple: net P/L ÷ initial money at risk.
- Sharpe and Sortino in this OS are calculated per trade on R multiples (mean ÷ standard deviation; mean ÷ downside deviation), not annualised.
- Recovery factor: net profit ÷ maximum drawdown.
- Edge credibility: the lower bound of the 95% confidence interval of mean R must be above zero, with at least 100 trades.

All of these are computed by `11_Performance_Analytics/tos_analytics.py`, not by hand or by AI.

## 8. Strategy specification template

Copy for each new strategy into `11_Performance_Analytics/Strategy_Analytics/<ID>/SPEC.md`:

```
ID / Name / Version / Date frozen
Market / Instrument / Timeframe / Session
Market conditions
Entry criteria / Confirmation
Stop-loss method / Take-profit method / Exit conditions
Position sizing method / Risk per trade
Risk/reward assumptions
Invalidating conditions / News restrictions
Backtest: data source, period, costs, n, expectancy R, PF, max DD, commit hash
Walk-forward out-of-sample: same metrics
Forward test: same metrics
Known weaknesses
Change log (each change = new version)
```
