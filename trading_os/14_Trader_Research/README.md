# 14 Trader Research

A tracker of traders whose results can be checked, what each of them actually does, and which part of it we are testing on XAUUSD. It exists to borrow proven ideas without borrowing anyone's hype.

The registry is `traders.json`. The same list is in the Trading OS Sheet (Trader Research and Strategy Lab tabs) and is refreshed weekly by a scheduled research task.

## How a trader gets onto the list

| Level | Evidence | What we do with it |
|---|---|---|
| A | Audited real-money competition (World Cup Trading Championships) or a regulator filing | Study the method; code any published rules |
| B | Platform-certified track record (for example Darwinex DarwinIA) | Use as a realism benchmark; strategies are usually anonymous |
| C | The trader published the method (book, paper) but the record is not independently checked here | Code the rules; the method stands or falls on our own backtest |
| D | Self-reported results, social media, course sellers | Lead only; never coded ahead of A to C material |

Screenshots, influencer P/L and "copy my trades" services never enter at A or B. Most of the "best trader" lists online are marketing; the World Cup standings page and certified platforms are the primary sources.

## What we take from each trader

| ID | Trader | Evidence | What we take | Our code |
|---|---|---|---|---|
| T-01 | Larry Williams | A: 1987 World Cup, 11,376% | Volatility breakout from the open | S-011 |
| T-02 | Andrea Unger | A: four World Cup titles | Simple breakouts; average trade must beat costs before anything else | S-012 |
| T-03 | Kevin Davey | A: World Cup 1st and two 2nds, 2005 to 2007 | The strategy factory: incubation and written quit rules | P-01 (gate) |
| T-04 | Michael Cook | A: World Cup 2007 and 2014 | Stops set by structure, sizing discipline | Research |
| T-05 | Linda Raschke | C: Street Smarts (1995) | Turtle Soup and Holy Grail | S-013, S-014 |
| T-06 | Toby Crabel | C: NR7 and opening range research (1990) | Contraction then expansion | S-015 |
| T-07 | Turtles (Curtis Faith) | C: published rules | 55-bar breakout, 2N stop | S-016 |
| T-08 | Paul Tudor Jones | C: interviews | 200-day defensive filter; 5:1 reward to risk | F-01 |
| T-09 | Patrick Nill | A: leads the 2026 World Cup forex division | Method unpublished; watch | None |
| T-10 | Darwinex leaders | B: certified monthly rankings | Benchmark for realistic returns and drawdowns | None |

## What a senior desk takes from this list

1. **The published methods are simple rules.** Breakouts of a prior range, failed breakouts, pullbacks in strong trends. What these winners have published is plain and testable, not an indicator stack. Our desk scoring stays, but every setup it fires must map to one of these plain, testable rules.
2. **Contest returns are not targets.** A 300% year in a contest comes with leverage that would breach a prop firm's daily limit in a week. Certified platform traders run near 10% per six months with about 6% drawdown. Our limits (D-008) are built for the second world.
3. **Process beats setup.** Davey's edge is the factory: many ideas in, few survive incubation, each with a written quit rule. That is now part of our gate.
4. **Costs first.** Unger checks that the average trade clears costs before anything else. On gold that means spread and slippage at news are tested at worse than measured.
5. **Our own sweep idea already has a published name.** Turtle Soup (S-013) is the mechanical version of the liquidity-sweep reversal; test it against our SWEEP definition before trusting either.

## How a researched idea becomes part of our strategy

```
Tracker entry (A to C) → rules written as a spec → coded in 05_Backtesting/gtos_backtest/champions.py
→ synthetic test (code runs, no look-ahead) → real XAUUSD data backtest → validation gate
→ incubation on demo (P-01) → router tickets on paper → only then any real money (D-003)
```

A famous name changes the order of testing, never the gate.

## Weekly refresh

Every Sunday the scheduled research task:

1. Re-reads each `record_source` and refreshes the figures, with the date read (World Cup standings change daily during the year).
2. Searches for new A or B evidence: World Cup standings, Darwinex rankings, newly published methods by existing names.
3. Adds new names only at level A to C, and marks anything else as a lead.
4. Updates the Trader Research tab in the Sheet, and lists the matching `traders.json` edits so they can be committed here.

Check the file at any time:

```powershell
py tools\trader_research.py check
py tools\trader_research.py stale --days 7
py tools\trader_research.py csv > traders.csv
```
