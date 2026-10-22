# Pre-registration: Indian ETF cross-asset momentum rotation

Committed before any backtest, IC calculation or return statistic was computed.
Anything changed after this commit is a new trial and must be recorded in
`results/TRIALS.md`.

Adapted from the ETF case study in *Machine Learning for Algorithmic Trading*
(3rd ed.), `case_studies/etfs`: long-only, rank-and-rebalance, monthly
month-end decisions, equal weight in the top k.

## 1. Hypothesis and mechanism

Indian sector, size-segment and asset-class indices that have outperformed
over the previous months keep outperforming over the next month.

The proposed mechanism has three parts:

- **Slow diffusion:** information about sector earnings cycles (credit growth
  for banks, commodity prices for metals and energy, the rupee for IT) is
  priced in gradually.
- **Flow persistence:** domestic SIP and mutual-fund flows, and foreign
  institutional flows, chase recent sector winners for months, not days.
- **Defensive rotation:** gold, government bonds and cash in the universe let
  the ranking leave equities when they trend down.

A cross-sectional momentum effect on sector and asset-class portfolios is
expected to be weak. The expected monthly cross-sectional rank IC is
**0.02–0.10**. An IC above 0.15 will be treated as evidence of a bug or
lookahead, not as success.

## 2. Universe (fixed list, eligibility point-in-time)

There are 18 assets: 14 equity indices, gold, silver, a 10-year government
bond index and overnight cash. Each one is researched on its NSE index, or on
the ETF itself for gold and silver, and is traded through the most liquid ETF
tracking it. The full list is in `config/strategy.yaml`.

An asset is eligible on a decision date only if:

- it has at least 252 sessions of price history up to that date, and
- it has a price on that date.

Known selection bias: the list was chosen in October 2026 because each asset
has an ETF that trades at least about ₹2 crore a day today. That uses current
knowledge of which ETFs exist and are liquid. Indices do not delist, so the
classic survivorship bias of stock universes does not apply. Some sector
indices before their launch dates are NSE back-calculations. This is
documented in the report, not corrected for.

## 3. Primary specification (the one being tested)

| Item | Value |
|---|---|
| Signal | Total return over the trailing 126 sessions (`ret_126`) |
| Decision | Close of the last NSE session of each month |
| Execution | Close of the next NSE session (one-session delay) |
| Selection | Top **4** eligible assets by signal |
| Weighting | Equal weight |
| Holding | Until the next monthly decision |
| Capital | ₹10,00,000 |

## 4. Exploration grid (every cell counts as a trial)

| Axis | Values |
|---|---|
| Score | total return; return divided by realized volatility (Sharpe-style) |
| Lookback | 63, 126, 252 sessions; 252 sessions skipping the most recent 21 |
| Top k | 3, 4, 5 |

That is **24 trials**, including the primary specification. All 24 are
reported. Selection-adjusted statistics (Sharpe haircut, Deflated Sharpe
Ratio, PBO) use N = 24. A walk-forward version that picks the grid cell with
the best trailing performance each year is reported as a 25th trial.

## 5. Periods

- **Development:** first eligible decision date through 2024-09-30. All
  research, the grid and all gates use this period only.
- **Holdout:** 2024-10-01 through the latest available date. It is run once,
  for the primary specification and for the walk-forward selection, after
  every development gate has been evaluated. It is not used to choose or tune
  anything.

## 6. Benchmarks

1. **EW:** equal weight across all eligible assets, rebalanced monthly, with
   the same costs and tax. This is the main hurdle.
2. **NIFTY 50:** buy and hold (total return), with tax on liquidation at the
   end.
3. **NIFTY200 Momentum 30:** buy and hold (price index), shown for reference
   only. It is a stock-level momentum product an investor could buy instead.

## 7. Costs and tax (modeled, not optional)

- **Explicit charges:** brokerage, STT, NSE transaction charge, SEBI fee,
  stamp duty, GST and DP charge, at current retail delivery rates.
- **Spread:** half-spread by ETF liquidity tier.
- **Market impact:** square-root model on the ETF's median daily traded value.
- **Tax:** capital-gains tax for a resident individual under the rules in
  force after 23-Jul-2024, applied to all history:
  - Equity ETFs: 20% short-term (held 12 months or less); 12.5% long-term
    above the ₹1.25 lakh exemption.
  - Gold and silver ETFs: slab rate if held 12 months or less; 12.5%
    long-term.
  - Debt and liquid ETFs: slab rate regardless of holding period.
  - Slab rate is 30%, with 4% cess on all tax.
  - Set-off and carry-forward follow Indian rules: short-term losses offset
    any gain; long-term losses offset long-term gains only.

The rules are applied as they stand today on purpose. The question is what
the strategy earns if deployed now.

## 8. Gates and kill criteria (development period)

| Gate | Pass condition |
|---|---|
| Data | Validation report clean: no duplicate dates; no stale runs of 5 or more sessions; extreme daily moves (above 20% for indices) explained; index-to-ETF tracking correlation above 0.95 where both exist |
| Signal | Primary signal's monthly rank IC has a Newey-West t-stat > 2.0 and the same sign in both halves of the development period |
| Strategy (primary) | Net-of-cost CAGR above EW; net-of-cost Sharpe above 0.5; max drawdown no worse than EW's |
| Selection | Deflated Sharpe Ratio of the primary spec's **active** monthly returns (strategy − EW) above 0.95 with N = 24; PBO (CSCV, 16 blocks) below 0.5 |
| Costs | Safety margin (gross active return ÷ annual cost drag) at least 2.5×; active return still positive at 3× base costs |
| Tax | After-tax CAGR above EW's after-tax CAGR (the deployment decision) |

**Kill:** if the signal gate fails, or both selection-gate tests fail, the
strategy is rejected and the result is reported as a negative finding. The
strategy is not re-specified to make it pass.

**Holdout pass:** after costs and tax, the active return over the holdout is
above 0. That is a sanity check, not statistical proof, with about 24 months
of data.
