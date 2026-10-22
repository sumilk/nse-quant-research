# India Strategy Lab

A research lab for systematic strategies on Indian markets, built on the
workflow in *Machine Learning for Algorithmic Trading* (3rd ed.).

Every strategy goes through the same steps:

1. A pre-registered hypothesis, committed before any backtest.
2. A data gate that checks the inputs.
3. A signal test using IC with Newey-West standard errors.
4. Backtests with no lookahead, Indian trading costs and Indian capital-gains
   tax.
5. Statistics that correct for having tried many variants: Deflated Sharpe
   Ratio, probability of backtest overfitting (PBO), Sharpe haircut and
   walk-forward selection.
6. A holdout period that can be run once.

## Strategies

| Strategy | Model | Idea | Development result |
|---|---|---|---|
| [`india_etf_momentum`](strategies/india_etf_momentum/) | `xsec_momentum` | Monthly top-4 rotation across 18 NSE index ETFs (sectors, sizes, gold, silver, G-Sec, cash) by 6-month return | **Rejected.** IC t 1.43, Deflated Sharpe 0.08, trails equal weight after costs and tax ([report](strategies/india_etf_momentum/results/REPORT_dev.md)) |
| [`india_etf_trend`](strategies/india_etf_trend/) | `ts_trend` | Hold each of 16 risky index ETFs only while its 12-month return beats cash; otherwise cash | **Rejected.** Max drawdown −31% vs −61% for equal weight, but signal t 0.37, Sharpe 0.41 vs 0.48, PBO 0.74, 3.9 pp a year behind after tax ([report](strategies/india_etf_trend/results/REPORT_dev.md)) |

Every strategy tested, and what it was chosen after, is in the [strategy ledger](strategies/LEDGER.md).

## Layout

```
strategies/<id>/            one folder per strategy
  HYPOTHESIS.md             pre-registration (commit before any backtest)
  config.yaml               universe, spec, grid, periods, costs, tax, gates
  results/                  reports and figures (artifacts/ is regenerated, not committed)
src/strategylab/
  data/      fetch.py       NSE + Yahoo downloads, cached in data/raw, shared by all strategies
             panel.py       per-strategy total-return panel on the NSE calendar
             validate.py    data gate
  engine/    schedule.py    decision schedule, point-in-time eligibility
             backtest.py    daily loop: next-session fills, FIFO lots, cash, tax
             costs.py       Indian delivery charges, spread, square-root impact
             tax.py         STCG/LTCG, set-off, carry-forward
  models/    xsec_momentum.py   cross-sectional momentum (rank, top k)
             ts_trend.py        time-series trend (each asset vs cash, off -> defensive asset)
  evaluation/ research.py   development gates, 24-trial grid, report
              holdout.py    one-shot holdout
              current.py    ranking on the latest close
              stats.py      performance, IC, DSR, PBO, bootstrap
  app/       dashboard.py   Streamlit dashboard (comparison view + per-strategy tabs)
  cli.py                    `lab` command
tests/                      unit tests and a headless dashboard test
```

## Usage

```bash
uv sync
uv run lab list
uv run lab fetch                                        # data for every strategy (cached)
uv run lab research --strategy india_etf_momentum       # development period
uv run lab current  --strategy india_etf_momentum       # ranking on the latest close
uv run streamlit run src/strategylab/app/dashboard.py   # http://localhost:8501
uv run pytest -q
```

Run the holdout only after reviewing every development gate, with nothing
changed since. It works once per strategy:

```bash
uv run lab holdout --strategy india_etf_momentum --i-have-not-changed-anything
```

## Adding a strategy

1. **Model:** if no existing model fits, add `src/strategylab/models/<model>.py`
   and register it in `models/__init__.py`. The model must define `Spec`,
   `grid`, `primary`, `signal`, `signal_key` and `weights`.
2. **Folder:** create `strategies/<id>/` with `config.yaml` (copy an existing
   one) and `HYPOTHESIS.md`.
3. **Pre-register:** commit the hypothesis, the grid and the gates before
   running anything.
4. **Run:** `uv run lab fetch`, then `uv run lab research --strategy <id>`.
5. **Record changes:** a change to the specification after seeing results is a
   new trial. Record it in `results/TRIALS.md`. Never edit the hypothesis to fit
   the results.

## Data

| Data | Source | Notes |
|---|---|---|
| Index prices | NSE `historicalOR/indicesHistory` | Price indices only |
| Dividend yields | NSE `indicesYield` | Published from 2015/16 |
| ETF prices | Yahoo Finance | Isolated bad prints (e.g. around the Dec-2019 unit splits) are removed and reported |
| Mutual-fund NAV | AMFI via api.mfapi.in | Used as cash proxy before ETFs existed; persistent jumps (e.g. a 2015 x100 face-value change) are undone and reported |

NSE does not publish total-return series through this API. Total return is
approximated as price return plus a daily dividend accrual from the published
yield.

## Limitations

- **Back-calculated index history:** NSE back-calculates sector index history
  before each index's launch date.
- **Liquidity known today:** universes are chosen by today's ETF liquidity, and
  today's traded value is applied to all history for spreads, impact and
  capacity.
- **Today's tax rules on all history:** the rules in force after July 2024 are
  applied throughout. Tax is paid once a year rather than in advance-tax
  instalments.
- **Index, not ETF:** strategies trade the index. ETF tracking difference
  (expense ratio included) is reported in each data-validation report, not
  deducted from returns.

## Docker

```bash
docker compose build
docker compose run --rm lab fetch
docker compose run --rm lab research --strategy india_etf_momentum
docker compose up -d dashboard            # http://localhost:8501
docker build --target test .              # unit tests inside the image
```

`data/` and `strategies/` are bind-mounted, so downloads and results are kept
on the host.

The container runs as UID 1000. On a Linux host, run `mkdir -p data` first so
the folder is writable.

NSE sometimes blocks requests from cloud or datacenter IP addresses. If
`fetch` fails on a server, download the data on a local machine and copy
`data/raw` across.
