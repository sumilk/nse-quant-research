"""Performance, signal and selection statistics.

Formulas follow the ml4t skills: risk-metrics (Sharpe/Sortino/Calmar/CVaR),
information-coefficient (cross-sectional rank IC, Newey-West t), deflated-sharpe
(per-period Sharpe, skew/kurtosis-adjusted), backtest-overfitting (haircut, CSCV PBO).
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.regression.linear_model import OLS

EULER = np.euler_gamma


# ----- performance -------------------------------------------------------------

def perf(nav: pd.Series, rf: pd.Series, after_tax_terminal: float | None = None) -> dict:
    validate_nav(nav)
    r = nav.pct_change(fill_method=None).iloc[1:]
    ex = r - rf.reindex(r.index).fillna(0)
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    dd = nav / nav.cummax() - 1
    trough = dd.idxmin()
    peak = nav.loc[:trough].idxmax()
    rec = nav.loc[trough:][nav.loc[trough:] >= nav.loc[peak]]
    dd_days = ((rec.index[0] if len(rec) else nav.index[-1]) - peak).days
    downside = np.sqrt(np.mean(np.minimum(ex, 0.0) ** 2) * 252)
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    var95 = np.percentile(r, 5)
    m = nav.resample("ME").last().pct_change().dropna()
    out = {
        "CAGR": cagr,
        "Vol": r.std() * np.sqrt(252),
        "Sharpe": ex.mean() / ex.std() * np.sqrt(252),
        "Sortino": ex.mean() * 252 / downside if downside > 0 else np.nan,
        "MaxDD": dd.min(),
        "Calmar": cagr / abs(dd.min()) if dd.min() < 0 else np.nan,
        "MaxDD_days": dd_days,
        "VaR95_d": var95,
        "CVaR95_d": r[r <= var95].mean(),
        "Win_m": (m > 0).mean(),
        "ProfitFactor_m": m[m > 0].sum() / -m[m < 0].sum() if (m < 0).any() else np.nan,
    }
    if after_tax_terminal is not None:
        out["CAGR_after_tax"] = (after_tax_terminal / nav.iloc[0]) ** (1 / years) - 1
    return out


def validate_nav(nav: pd.Series) -> None:
    if len(nav) < 2 or not np.isfinite(nav.to_numpy()).all() or (nav <= 0).any():
        raise ValueError("NAV must contain at least two finite positive observations, without gaps")
    if not nav.index.is_monotonic_increasing or nav.index.has_duplicates:
        raise ValueError("NAV dates must be ordered and unique")


def monthly(nav: pd.Series) -> pd.Series:
    validate_nav(nav)
    return nav.resample("ME").last().pct_change(fill_method=None).iloc[1:]


# ----- signal ---------------------------------------------------------------------

def newey_west_t(x: pd.Series | np.ndarray, lags: int) -> float:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    fit = OLS(x, np.ones(len(x))).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return float(fit.tvalues[0])


def ic_series(score: pd.DataFrame, fwd: pd.DataFrame, elig: pd.DataFrame, dates, min_assets: int = 5) -> pd.Series:
    out = {}
    for d in dates:
        ok = elig.loc[d] & score.loc[d].notna() & fwd.loc[d].notna()
        if ok.sum() >= min_assets:
            out[d] = stats.spearmanr(score.loc[d][ok], fwd.loc[d][ok])[0]
    return pd.Series(out, dtype=float)


# ----- selection --------------------------------------------------------------------

def expected_max_sr(sr_std: float, n_trials: int) -> float:
    return sr_std * ((1 - EULER) * stats.norm.ppf(1 - 1 / n_trials)
                     + EULER * stats.norm.ppf(1 - 1 / (n_trials * np.e)))


def deflated_sharpe(returns: pd.Series, trial_srs: np.ndarray) -> dict:
    """returns: per-period series of the chosen strategy; trial_srs: per-period SRs of all trials."""
    r = returns.dropna()
    sr = r.mean() / r.std()
    e_max = expected_max_sr(np.std(trial_srs, ddof=1), len(trial_srs))
    skew, kurt = stats.skew(r), stats.kurtosis(r, fisher=False)
    se = np.sqrt((1 - skew * sr + (kurt - 1) / 4 * sr**2) / (len(r) - 1))
    return {"sr": sr, "e_max_sr": e_max, "dsr": float(stats.norm.cdf((sr - e_max) / se)),
            "psr_vs_0": float(stats.norm.cdf(sr / se)), "skew": skew, "kurtosis": kurt, "n": len(r)}


def pbo_cscv(perf_matrix: pd.DataFrame, n_blocks: int) -> dict:
    """Probability of backtest overfitting (Bailey et al. CSCV). perf_matrix: T x N per-period returns."""
    m = perf_matrix.dropna().to_numpy()
    blocks = np.array_split(np.arange(len(m)), n_blocks)
    logits, degradation = [], []
    n = m.shape[1]
    for test in combinations(range(n_blocks), n_blocks // 2):
        tr = np.concatenate([blocks[b] for b in range(n_blocks) if b not in test])
        te = np.concatenate([blocks[b] for b in test])
        sr_is = m[tr].mean(0) / m[tr].std(0, ddof=1)
        sr_oos = m[te].mean(0) / m[te].std(0, ddof=1)
        best = int(np.argmax(sr_is))
        rank = stats.rankdata(sr_oos)[best] / (n + 1)  # relative rank in (0,1), 1 = best
        logits.append(np.log(rank / (1 - rank)))
        degradation.append((sr_is[best], sr_oos[best]))
    logits = np.array(logits)
    deg = np.array(degradation)
    return {"pbo": float((logits <= 0).mean()), "n_splits": len(logits),
            "median_logit": float(np.median(logits)),
            "is_sr_best_mean": float(deg[:, 0].mean()), "oos_sr_best_mean": float(deg[:, 1].mean())}


def haircut_sharpe(best_ann_sr: float, trial_ann_srs: np.ndarray) -> float:
    return best_ann_sr - expected_max_sr(np.std(trial_ann_srs, ddof=1), len(trial_ann_srs))


def block_bootstrap_p(x: pd.Series, draws: int, block: int = 6, seed: int = 7) -> float:
    """One-sided p-value for mean > 0: circular block bootstrap of the series centred on zero."""
    v = x.dropna().to_numpy()
    n = len(v)
    rng = np.random.default_rng(seed)
    centred = v - v.mean()
    starts = rng.integers(0, n, size=(draws, int(np.ceil(n / block))))
    idx = (starts[:, :, None] + np.arange(block)) % n
    boot = centred[idx.reshape(draws, -1)[:, :n]].mean(1)
    return float((boot >= v.mean()).mean())


def ts_spread_series(signal: pd.DataFrame, fwd_excess: pd.DataFrame, elig: pd.DataFrame, dates) -> pd.DataFrame:
    """Per decision: mean forward excess return of assets whose signal is on (> 0) minus those off.

    Rows where every asset is on (or every asset is off) have no spread and are dropped.
    """
    out = {}
    for d in dates:
        ok = elig.loc[d] & signal.loc[d].notna() & fwd_excess.loc[d].notna()
        s, f = signal.loc[d][ok], fwd_excess.loc[d][ok]
        on, off = f[s > 0], f[s <= 0]
        out[d] = {"spread": on.mean() - off.mean() if len(on) and len(off) else np.nan,
                  "on_mean": on.mean() if len(on) else np.nan, "off_mean": off.mean() if len(off) else np.nan,
                  "share_on": len(on) / len(f) if len(f) else np.nan}
    return pd.DataFrame(out).T.astype(float)


def sharpe_diff_bootstrap_p(a: pd.Series, b: pd.Series, draws: int, block: int = 6, seed: int = 7) -> float:
    """One-sided p-value for Sharpe(a) > Sharpe(b): paired circular block bootstrap, share of draws <= 0."""
    df = pd.concat([a, b], axis=1).dropna().to_numpy()
    n = len(df)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(draws, int(np.ceil(n / block))))
    idx = ((starts[:, :, None] + np.arange(block)) % n).reshape(draws, -1)[:, :n]
    x = df[idx]                                   # draws x n x 2
    sr = x.mean(1) / x.std(1, ddof=1)
    return float((sr[:, 0] - sr[:, 1] <= 0).mean())
