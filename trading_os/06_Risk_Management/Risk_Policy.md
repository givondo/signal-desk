# Risk Policy

Version 1.0, effective 13 November 2026. Machine-readable limits live in `risk_limits.json`; this document explains them. The JSON file and this policy must always agree.

Risk limits are hard constraints. A rule that is broken is logged in `07_Trading_Journal/Rule_Violations.csv`, whether the trade won or lost.

## 1. Authority

- Risk management overrides any trading opportunity.
- No software in this OS places, modifies or closes orders. Calculators advise; you execute.
- AI may explain, summarise and challenge. It never sets stops, changes size or increases exposure.
- Limits can be tightened at any time. Loosening a limit needs a dated decision in `13_Decisions_and_Reviews/Trading_Decisions.md` backed by at least 100 journaled trades with positive expectancy at the current level.

## 2. Personal account limits

| Rule | Limit |
|---|---|
| Risk per trade | 0.5% of equity (hard cap 1.0%; the calculator refuses more) |
| Maximum aggregate open risk | 1.0% of equity |
| Maximum correlated risk (same USD direction) | 0.75% of equity |
| Maximum simultaneous trades | 2 |
| Stop-loss | Mandatory at entry, placed on the server, never widened |
| Daily loss limit | 1.5% of start-of-day equity → stop for the day, platform closed |
| Weekly loss limit | 3.0% of start-of-week equity → stop for the rest of the week |
| Consecutive losses | 3 in a day → stop for the day |
| Drawdown review | 8% from equity peak → stop live trading, return to demo, written review |
| Drawdown halt | 15% from equity peak → full stop for 30 days, strategy re-validation |
| Minimum strategy expectancy | Strategy must have passed the validation gate (+0.15R out-of-sample) |

## 3. Prop account limits

The firm's own rules are the outer wall; these internal rules sit well inside it.

| Rule | Limit |
|---|---|
| Risk per trade | 0.5% of initial balance (hard cap 1.0%; at FTMO treat 1.0% including slippage as an absolute ceiling) |
| Internal daily stop | 40% of the firm's daily loss allowance |
| Risk reduction | When 50% of the maximum loss allowance is used, risk drops to 0.25% per trade |
| Account stop | When 75% of the maximum loss allowance is used, stop trading that account and review |
| Simultaneous trades | 2 |
| Consecutive losses | 3 in a day → stop for the day |
| Failed evaluations | 2 consecutive → back to demo for at least four weeks |
| Firm methodology | Always use the firm's current calculation: equity vs balance, reset time, trailing vs static, inclusion of floating P/L, swaps and commissions |

Worked example, FTMO 2-Step $100,000 (rules read 13 Nov 2026):

| Item | Amount |
|---|---|
| Firm daily allowance | $5,000 from the day's opening balance |
| Internal daily stop (40%) | $2,000 |
| Risk per trade (0.5%) | $500 |
| Losing trades to internal stop | 4, but the 3-loss rule stops you first |
| Firm maximum loss | $10,000 static (floor $90,000) = 20 losses at 0.5% |
| Risk reduced to 0.25% at | equity $95,000 |
| Account stop at | equity $92,500 |

Run `python Drawdown_Calculator.py --preset FTMO_2STEP --initial 100000 --equity <now> --day-start-balance <today's opening balance>` before each session.

## 4. Daily and weekly protection

- **Before the session:** know today's daily floor, internal stop and remaining allowance in money, not percent.
- **Unexpected events** (flash moves over 2 × ATR in a minute, platform or connectivity failure, surprise central bank action, geopolitical shock): flatten or reduce to zero new risk, stop for the session, journal the event.
- **News:** no new entries from 15 minutes before to 15 minutes after tier-1 USD releases (CPI, NFP, FOMC, PCE, GDP advance, retail sales), and stricter where a firm requires (The5ers: no orders 2 minutes either side).
- **Weekend:** personal account flat by Friday 23:00 EAT unless the strategy specification explicitly allows a hold. Prop accounts follow firm rules and the strategy spec, whichever is stricter.

## 5. Revenge trading and recovery rules

1. After any loss: a 15-minute break away from the screen before the next entry.
2. After 2 consecutive losses: a 60-minute break and a written note in `Psychology_Log.csv`.
3. After 3 consecutive losses or the daily limit: done for the day. The platform is closed.
4. Size never increases after a loss. Size changes only at a monthly review, via a decision entry.
5. No new strategy, instrument or timeframe on a losing day.
6. The goal after a losing day is clean execution, not recovering the money.

## 6. Cooling-off periods

| Trigger | Cooling-off |
|---|---|
| Daily loss limit hit | 24 hours |
| Weekly loss limit hit | 3 trading days, review completed before return |
| Any rule violation | Next session at half risk |
| Two violations in a week | Demo only until the weekly review signs off |
| Drawdown review level (8%) | Demo until a written review and a strategy re-check |

## 7. Risk dashboard (what to see each session)

Current equity; daily realised P/L; daily floating P/L; daily drawdown; weekly drawdown; maximum historical drawdown; remaining loss allowance (daily and maximum); open risk; risk per instrument; correlated exposure; distance to the account failure threshold. Status is OK below 50% of an allowance used, WARN at 50% (or when the internal stop is hit), CRITICAL at 75%, BREACHED at the floor.

`Drawdown_Calculator.py` produces these figures for prop accounts; `tos_analytics.py` produces the historical ones from the journal.
