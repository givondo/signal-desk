# Learning Roadmap

Created 13 November 2026. Assumes 6 to 8 hours a week (to be confirmed, question Q10), split roughly into one third screen time, one third study, one third journaling and review. Progress is measured by evidence in `Competency_Tracker.csv`, not by courses finished.

Avoid any course or mentorship sold on income claims, "funded in 30 days" promises, signal subscriptions or lifestyle marketing. Prefer books, primary sources and work you can check.

## Months 1 to 2: process and measurement

| Area | Study | Practical proof |
|---|---|---|
| Risk management | This OS: Risk Policy, both calculators; Van Tharp's position-sizing chapters in *Trade Your Way to Financial Freedom* | Size 20 hypothetical trades by hand, check against `Position_Sizing.py` |
| Statistics and probability | Khan Academy statistics (sampling, confidence intervals); *Fooled by Randomness* (Taleb) | Explain why 30 trades cannot prove an edge, using your own journal |
| Trading psychology | *Trading in the Zone* (Douglas) | 4 weekly psychology reviews completed |
| Journal discipline | `07_Trading_Journal` | 40+ demo trades fully journaled with screenshots |

## Months 3 to 4: testing properly

| Area | Study | Practical proof |
|---|---|---|
| Evidence-based technical analysis | *Evidence-Based Technical Analysis* (Aronson) | S-001 tested on real data with costs, results filed |
| Backtesting and validation | *Quantitative Trading* (Ernest Chan); Signal Desk docs | Walk-forward run with frozen parameters and a random-entry baseline |
| Python for trading | pandas, NumPy; Signal Desk code | Add one indicator or test to Signal Desk with a passing unit test |
| Macroeconomics for gold | Federal Reserve statements and minutes (primary source); BLS release notes | Write a one-page monthly macro note on gold drivers |

## Months 5 to 6: execution and markets

| Area | Study | Practical proof |
|---|---|---|
| Market microstructure | *Trading and Exchanges* (Larry Harris), chapters on liquidity, spreads and order types | Measure your real spread and slippage on 30 trades |
| Technical analysis reference | *Technical Analysis of the Financial Markets* (Murphy), as a reference only | Define every chart concept you use as a testable rule |
| Portfolio and risk | Correlation and position limits in this OS | Correlated exposure never exceeded in the month |
| Financial reporting | `05_Trading_Financial_Performance.md` | Two consecutive fully reconciled monthly reports |

## Months 7 to 12: specialise

- Second strategy through the full validation gate.
- *Advances in Financial Machine Learning* (López de Prado), chapters on backtest overfitting, only after the basics are solid.
- Algorithmic research: automate data capture and reporting in Signal Desk; execution stays manual.
- Communities: prefer forums where people post full statistics (for example quantitative finance communities) over social media signal groups.

## Competency levels used in the tracker

0 = not started; 1 = understands the idea; 2 = applies with help or a checklist; 3 = applies reliably, evidence in records; 4 = can teach it and has 3+ months of evidence.
