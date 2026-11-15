# Research integrity corrections — 23 October 2026

The two strategies retain their original primary parameters, exploration grids,
benchmarks and decision schedules. Their original development reports are
preserved under each strategy's `results/legacy_review/`. Those results are
invalidated, not replaced with a new profitability claim.

## Corrected behavior

- Development sources are cut off at `dev_end` before cleaning, validation,
  liquidity estimation or signal construction. No future dividend yield is
  backfilled. Before published yields, equity index proxies use price returns.
- Production data cleaning uses explicit reviewed bad-print exclusions and a
  declared AMFI face-value correction. It does not use a centered future-price
  median or infer new corporate actions from future prices. The older generic
  cleaning helpers remain utilities; they are not used by panel construction.
- A quote can be carried for valuation for at most two NSE sessions. Its absence
  is retained in a separate observed-quote mask. It cannot be an execution price.
  Longer gaps fail validation; missing held-asset valuations and invalid NAV
  stop simulation. Performance functions reject NAV gaps rather than drop them.
- `--mode index_proxy` (the default) retains long index histories as hypothetical
  exposure research. It uses an explicit fixed ADV assumption of Rs 1 crore per
  asset for cost scenarios. This is an assumption, not measured historical
  liquidity or evidence of historical ETF tradability.
- `--mode etf` uses observed ETF adjusted-close histories for every configured
  asset, without pre-listing index backfills. Eligibility requires the existing
  history minimum, a fresh quote and positive historical liquidity. Trade costs
  use a trailing 63-session median of traded value with at least 21 observations,
  lagged one session. Both strategies and benchmarks use these data boundaries.
  ETF reports go under `results/etf/`, separately from index-proxy reports.
- The ETF mode is an adjusted-return replication. It still uses fractional
  synthetic units and modeled close executions; it is not a broker order replay.
  Vendor distribution adjustments, particularly LIQUIDBEES, must be verified
  before those returns can represent an investable cash total-return series.
- Prior-year tax is paid on the first session of the next financial year.
  Funding sales are sized using actual modeled net proceeds, and their gains
  are recorded on their actual sale dates. Daily cash and invested values are
  exposed in results; negative cash is rejected after each operation.
- Sortino uses downside RMS relative to the cash target across all observations.
  The same formula is used in the reports and dashboard.
- Each rerun marks its previous artifacts invalid before it starts. A failed
  data gate blocks the run. The dashboard shows the failure rather than stale
  results, and holdout requires a valid corrected development run.

## Holdout disclosure

The 2024-10 onward window was previously used for liquidity estimation and data
validation. Although no holdout backtest was executed, it is **not a pristine
holdout**. Correcting the pipeline does not reverse that exposure. A future run
of that window must remain labeled accordingly. No holdout evaluation was run
while making these corrections.

## Real-data reruns

Both index-proxy development runs stop at missing NSE prices: PSU Bank in June
2010 and Oil & Gas in January 2020. A targeted request to the original NSE
historical endpoint recovered no additional observations. ETF-mode runs also
fail on gaps and stale runs in cached ETF histories. Details are in each mode's
`data_validation.md`. No values were interpolated to make a gate pass, and the
universe or strategy rules were not tuned to remove the failures.

The next requirement is a verified replacement data source for the failed
histories and a verified total-return feed for distributing cash ETFs. If a
shorter sample or different universe is chosen instead, record that as a new
research specification and trial, not an infrastructure repair.

## Commands

```sh
uv run python -m pytest -q
uv run lab research --strategy india_etf_momentum --mode index_proxy
uv run lab research --strategy india_etf_trend --mode index_proxy
uv run lab research --strategy india_etf_momentum --mode etf
uv run lab research --strategy india_etf_trend --mode etf
```

A nonzero exit and a blocked report are expected until the source gaps are
repaired. The development input cutoff remains 30 September 2024.
