# Pre-registration: Indian ETF time-series trend (absolute momentum)

Committed before the model was written and before any backtest, signal
statistic or return figure for this strategy was computed. Anything changed
after this commit is a new trial and must be recorded in `results/TRIALS.md`.

## 0. Disclosure: this is not an independent test

This strategy was proposed **after** the development-period results of
`india_etf_momentum` were seen. That strategy used the same assets, the same
data and the same 2006–2024 window, and it was rejected. Those results showed
that the equal-weight portfolio of these assets and the momentum rotation both
fell about 57% in the 2008 crisis. That drawdown is what motivates this
strategy.

The development period is therefore not unseen. Selection across strategies is
recorded in `strategies/LEDGER.md`, where this is strategy 2 on this data. The
holdout window (from 2024-10-01) has not been used by any strategy.

## 1. Hypothesis and mechanism

Holding each Indian sector, size and commodity index only while its own
12-month return beats cash, and holding cash otherwise, cuts drawdowns
substantially. It also improves risk-adjusted return compared with holding the
same assets all the time.

The mechanism is that sustained drawdowns in Indian equity segments (2008,
2011, 2015–16 for PSU banks and metals, 2018 for small and mid caps, 2020)
unfold over months, not days, as earnings downgrades and foreign outflows
build. A slow trend filter exits after part of the fall and stays out for the
rest.

The expected cost is lower return in strong bull markets, and losses from
whipsaws in sideways markets. Under Indian tax rules, each exit within 12
months of buying also realizes short-term gains taxed at 20%.

The claim is about **risk**, not return. The strategy is not expected to beat
equal weight on CAGR.

## 2. Universe

- **Risky assets (16):** NIFTY 50, NIFTY NEXT 50, MIDCAP 150, SMALLCAP 250,
  BANK, IT, PHARMA, FMCG, AUTO, METAL, OIL & GAS, REALTY, PSU BANK and CPSE
  (NSE indices with dividend-yield accrual), plus GOLDBEES and SILVERBEES
  (ETF prices). This is the same universe as `india_etf_momentum` without its
  two defensive assets.
- **Defensive assets** (not trend-tested):
  - **CASH:** HDFC Liquid Fund, Regular Plan, Growth (AMFI scheme 100868). The
    daily NAV is net of fees and starts 2006-04. It is traded as LIQUIDBEES for
    costs and capacity.
  - **GSEC10Y:** NSE NIFTY 10 yr Benchmark G-Sec index, traded as LTGILTBEES.

Eligibility is point-in-time: at least 252 sessions of history, and a price on
the decision date.

## 3. Rule

At each monthly decision (close of the last NSE session of the month):

1. Let R be the eligible risky assets whose signal is defined. Each gets a
   weight of 1/|R|.
2. A risky asset is **on** if its signal is positive and **off** otherwise.
3. Every off asset's weight goes to the defensive asset. Weights always sum to 1.
4. Trades execute at the close of the next session. The book is held until the
   next decision.

**Primary specification:**

| Item | Value |
|---|---|
| Signal | **excess**: the asset's total return over the trailing 252 sessions minus CASH's return over the same window |
| Defensive asset | **CASH** |
| Capital | ₹10,00,000 |

## 4. Exploration grid (every cell is a trial)

| Axis | Values |
|---|---|
| Rule | **excess** (trailing return minus cash, compared with 0); **sma** (total-return level above its own L-session simple moving average, including the decision day) |
| Lookback L | 126, 252 sessions |
| Defensive asset | CASH, GSEC10Y |

That is **8 trials**, including the primary. All 8 are reported. Deflated
Sharpe and PBO use N = 8. Walk-forward selection over the grid is reported as
trial 9.

## 5. Periods

- **Development:** the first decision date on which every trial is defined
  (expected around 2007, so the 2008 crisis is included) through 2024-09-30.
- **Holdout:** 2024-10-01 to the latest available date. It is run once, for
  the primary and the walk-forward pick, after the development gates have been
  reviewed.

## 6. Benchmarks

1. **EW (risky):** equal weight across the eligible risky assets, always fully
   invested, rebalanced monthly, with the same costs and tax. This is the same
   strategy with the trend filter switched off, and it is the main hurdle.
2. **NIFTY 50:** buy and hold (total return), with tax on liquidation at the
   end.

## 7. Costs and tax

The same model as `india_etf_momentum`, rules in force after 23-Jul-2024 and
applied to all history:

- **Trading costs:** explicit charges, a half-spread by ETF liquidity tier, and
  square-root market impact.
- **Equity ETFs:** 20% short-term tax (held 12 months or less); 12.5% long-term
  above ₹1.25 lakh.
- **Gold and silver:** slab rate short-term; 12.5% long-term.
- **Liquid and gilt ETFs:** slab rate.
- **Slab rate:** 30%, plus 4% cess on all tax.
- **Losses:** set-off and carry-forward follow Indian rules.

## 8. Gates (development period)

| Gate | Pass condition |
|---|---|
| Data | Same checks as `india_etf_momentum`. Extreme moves must be explained; waivers are documented in the config |
| Signal (time series) | For each decision date, take the mean forward excess-over-cash return of **on** assets minus that of **off** assets, from that execution to the next. The mean of this spread has a Newey-West t-stat > 2.0 and the same sign in both halves of the development period |
| Strategy (net of costs, pre-tax, primary) | Max drawdown no deeper than **2/3 of EW's**; Sharpe above EW's; Calmar above EW's |
| Selection | Deflated Sharpe Ratio of the primary's monthly excess-over-cash returns > 0.95 (N = 8); PBO (CSCV, 16 blocks, on the 8 trials' monthly excess returns) < 0.5; block-bootstrap one-sided p-value for (primary Sharpe − EW Sharpe) > 0 below 0.10 |
| Costs | At 3× base costs, the primary's Sharpe is still above EW's |
| Tax | After-tax CAGR no more than **2.0 percentage points a year** below EW's after-tax CAGR, and after-tax max drawdown no deeper than 2/3 of EW's |

**Kill:** the strategy is rejected if any of these hold:

- the signal gate fails;
- the drawdown condition of the strategy gate fails (it is the core claim);
- both the Deflated Sharpe and the PBO tests fail.

The strategy is not re-specified to make it pass.

**Holdout pass:** after costs and tax, max drawdown no deeper than EW's, and
CAGR no more than 2 percentage points a year below EW's. This is a sanity
check over about 24 months, not statistical proof.
