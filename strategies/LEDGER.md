# Strategy ledger

Every strategy tested in this lab, in the order it was pre-registered.

Gates and Deflated Sharpe correct for the trials **within** one strategy. They
do not correct for picking among strategies. Strategies tested on the same
data are not independent: each later one was chosen knowing how the earlier
ones did. Read any "pass" here alongside the number of strategies tried on
that data.

| # | Strategy | Pre-registered | Data and development window | Trials | Informed by | Status |
|---|---|---|---|---|---|---|
| 1 | [india_etf_momentum](india_etf_momentum/) | 2026-10-22 | NSE sector, size and asset indices; 2006–2024 | 24 + walk-forward | none | Rejected (development) |
| 2 | [india_etf_trend](india_etf_trend/) | 2026-10-22 | Same assets and window as #1; cash from AMFI liquid-fund NAV | 8 + walk-forward | #1 (its 2008 drawdown) | Rejected (development): drawdown halved, but signal t 0.37, Sharpe below EW, PBO 0.74 |

Strategies tested on the same assets and window: **2**.

Holdout (from 2024-10-01) spent by: none.

## Infrastructure correction — 23 October 2026

The prior development metrics and rejection evidence are preserved in each
strategy's `results/legacy_review/` but invalidated by the data/accounting
review. Primary parameters and grids are unchanged. Corrected index-proxy and
ETF-mode development runs are blocked at their data gates; current reports
list the source gaps. No new holdout backtest was run. The original window is
not pristine: earlier costs and validation used observations from that window.
See [correction details](../docs/RESEARCH_INTEGRITY.md).
