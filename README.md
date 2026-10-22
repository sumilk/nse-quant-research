# India ETF cross-asset momentum

This project tests one strategy on Indian index ETFs. Each month it ranks 18
assets (NSE sector and size indices, gold, silver, a 10-year government bond
index and overnight cash) by recent total return. It buys the top 4 in equal
weight and rebalances at month-end. It is adapted from the ETF case study in
*Machine Learning for Algorithmic Trading* (3rd ed.).

The hypothesis, universe, parameter grid, holdout window and pass/fail gates
were fixed in [HYPOTHESIS.md](HYPOTHESIS.md) and committed before any backtest
was run.

## Running it

```bash
uv sync
uv run python -m india_etf_momentum.data        # download NSE + Yahoo data (cached in data/raw)
uv run python -m india_etf_momentum.research    # development period: gates, 24 trials, report
uv run python -m india_etf_momentum.current     # ranking on the latest close
uv run streamlit run src/india_etf_momentum/dashboard.py
uv run pytest -q
```

When you have reviewed the development gates and changed nothing, run the
holdout. It can be run **once**:

```bash
uv run python -m india_etf_momentum.holdout --i-have-not-changed-anything
```

## What the pipeline checks

| Check | Where |
|---|---|
| Pre-registered hypothesis, kill criteria, trial count | `HYPOTHESIS.md`, `results/TRIALS.md` |
| Data quality: OHLC consistency, gaps, stale runs, extreme moves, index-vs-ETF tracking | `validate.py` → `results/data_validation.md` |
| No lookahead: signals read only closes up to the decision date; fills happen at the next session's close; a unit test changes future prices and confirms the signal does not move | `signals.py`, `tests/` |
| Survivorship and universe: point-in-time eligibility (252 sessions of history); the remaining selection bias is documented | `signals.eligibility`, `HYPOTHESIS.md` |
| Signal: monthly cross-sectional rank IC, Newey-West t-stat, both halves, decay | `research.py` |
| Overfitting: all 24 grid cells reported, Deflated Sharpe Ratio, Sharpe haircut, probability of backtest overfitting (CSCV), walk-forward selection, a holdout usable once | `stats.py`, `research.py`, `holdout.py` |
| Costs: STT, exchange and SEBI fees, stamp duty, GST, DP charge, half-spread by liquidity tier, square-root impact; 0–3× sensitivity; safety margin; capacity | `costs.py`, `research.py` |
| Tax: STCG/LTCG for equity, gold/silver and debt ETFs; set-off; carry-forward; tax paid at each financial-year end; tax on liquidation | `tax.py`, `backtest.py` |

## Data

| Data | Source | Notes |
|---|---|---|
| Index prices | NSE `historicalOR/indicesHistory` | Price indices only |
| Dividend yields | NSE `indicesYield` | Published from 2015/16 |
| Gold, silver and ETF prices | Yahoo Finance | Also used for ETF liquidity |

NSE does not publish total-return series through this API. Total return is
approximated as price return plus a daily dividend accrual from the published
yield.

## Limitations

- **Back-calculated index history:** NSE back-calculates sector index history
  before each index's launch date.
- **Liquidity known today:** the universe was chosen by today's ETF liquidity,
  and today's traded value is applied to all history for spreads, impact and
  capacity.
- **Today's tax rules on all history:** the rules in force after July 2024 are
  applied throughout. Tax is paid once a year rather than in advance-tax
  instalments.
- **Index, not ETF:** the research trades the index. ETF tracking error and
  expense ratios are reported in the data-validation report, not deducted from
  returns.

## Docker

```bash
docker compose build
docker compose run --rm pipeline india_etf_momentum.data
docker compose run --rm pipeline india_etf_momentum.research
docker compose run --rm pipeline india_etf_momentum.current
docker compose up -d dashboard            # http://localhost:8501
docker build --target test .              # unit tests inside the image
```

`data/` and `results/` are bind-mounted, so downloads and results are kept
when the container is removed. `config/` and `HYPOTHESIS.md` are mounted
read-only.

The container runs as UID 1000. On a Linux host, create the two folders first
with `mkdir -p data results` so they are writable. Docker Desktop on macOS
handles this automatically.

NSE sometimes blocks requests from cloud or datacenter IP addresses. If the
`data` job fails on a server, download the data on a local machine and copy
`data/raw` across.
