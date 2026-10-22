"""Build a strategy's daily total-return panel on the NSE trading calendar.

Calendar: the sessions of the strategy's calendar_master index (calendar-ops: one
exchange-specific calendar, never generic business days).

Total return for NSE price indices = price return + dividend accrual. The
accrual for session t uses the dividend yield published for session t-1
(point-in-time: today's yield is only known after today's close). Before NSE
published yields (~2015) the asset's median yield over its first published year
is used and flagged in the validation report.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from strategylab.config import ROOT
from strategylab.data.fetch import etf_path, nse_path, nsedy_path

SESSIONS_PER_YEAR = 252
MAX_FFILL_SESSIONS = 2
FALLBACK_CASH_RATE = 0.06  # risk-free rate only, before a cash asset has history

INDEX_SCHEMA = {"date": "datetime64[ns]", "open": "float64", "high": "float64",
                "low": "float64", "close": "float64"}
DY_SCHEMA = {"date": "datetime64[ns]", "dy_pct": "float64"}
ETF_SCHEMA = {"date": "datetime64[ns]", "close": "float64", "adj_close": "float64", "volume": "float64"}


def _drop_spikes(px: pd.DataFrame, window: int = 11, factor: float = 3.0) -> tuple[pd.DataFrame, list]:
    """Remove prints more than `factor`x away from the centred rolling median.

    Yahoo's NSE ETF history has sessions left on the wrong side of a unit split
    (e.g. 2019-12-19/20 for NIFTYBEES, BANKBEES, GOLDBEES at 1/10 or 1/100 scale).
    """
    med = px["close"].rolling(window, center=True, min_periods=3).median()
    ratio = px["close"] / med
    bad = (ratio > factor) | (ratio < 1 / factor)
    return px[~bad], list(px.index[bad])


def _load(path, schema: dict) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"Missing {path.relative_to(ROOT)}. Run `uv run lab fetch` first.")
    df = pd.read_parquet(path)
    for col, dtype in schema.items():
        assert col in df.columns, f"{path.name}: missing column {col}"
        if len(df):
            df[col] = df[col].astype(dtype)
    assert not df["date"].duplicated().any(), f"{path.name}: duplicate dates"
    return df


@dataclass
class Panel:
    calendar: pd.DatetimeIndex
    returns: pd.DataFrame          # daily total returns, sessions x asset (NaN before first price)
    levels: pd.DataFrame           # total-return levels, NaN before the first price
    rf: pd.Series                  # daily risk-free return
    index_ohlc: dict[str, pd.DataFrame]
    dy_fallback: dict[str, tuple[pd.Timestamp | None, float]]
    ffilled: dict[str, int]
    etf: dict[str, pd.DataFrame]   # ETF ticker -> prices (spikes removed)
    etf_spikes: dict[str, list]    # ETF ticker -> dates of removed prints
    etf_adv_cr: pd.Series          # asset id -> median daily traded value of its ETF, last year, Rs crore
    reference: dict[str, pd.Series]  # reference benchmarks (price series, no costs or tax)


def build_panel(cfg: dict) -> Panel:
    uni = cfg["universe"]
    master_index = next(a["index"] for a in uni["assets"] if a["id"] == uni["calendar_master"])
    master = _load(nse_path(master_index), INDEX_SCHEMA)
    cal = pd.DatetimeIndex(master["date"]).sort_values()
    cal = cal[cal >= pd.Timestamp(cfg["periods"]["data_start"])]

    etf, etf_spikes = {}, {}
    for a in uni["assets"]:
        if a.get("etf"):
            etf[a["etf"]], etf_spikes[a["etf"]] = _drop_spikes(_load(etf_path(a["etf"]), ETF_SCHEMA).set_index("date"))
    rets, ohlc, dy_fallback, ffilled = {}, {}, {}, {}

    for a in uni["assets"]:
        aid = a["id"]
        if a["source"] in ("nse", "nse_tr"):
            px = _load(nse_path(a["index"]), INDEX_SCHEMA).set_index("date")
            ohlc[aid] = px
            close = px["close"]
        elif a["source"] == "yahoo":
            close = etf[a["etf"]]["adj_close"]
        else:
            raise ValueError(f"{aid}: unknown source {a['source']}")

        on_cal = close.reindex(cal)
        first = on_cal.first_valid_index()
        if first is None:
            raise ValueError(f"{aid}: no prices on the calendar")
        live = on_cal.loc[first:]
        ffilled[aid] = int(live.isna().sum())
        live = live.ffill(limit=MAX_FFILL_SESSIONS)
        r = live.pct_change()

        if a["source"] == "nse":
            dy = _load(nsedy_path(a["index"]), DY_SCHEMA)
            if dy.empty:
                dy_series = pd.Series(0.0, index=live.index)
                dy_fallback[aid] = (None, np.nan)
            else:
                dy = dy.set_index("date")["dy_pct"].sort_index()
                first_dy = dy.index[0]
                fill = float(dy.loc[: first_dy + pd.Timedelta(days=365)].median())
                dy_series = dy.reindex(live.index, method="ffill").fillna(fill)
                dy_fallback[aid] = (first_dy, fill)
            # Yield known at the close of t-1 accrues over session t.
            r = r + dy_series.shift(1) / 100 / SESSIONS_PER_YEAR
        rets[aid] = r.reindex(cal)

    returns = pd.DataFrame(rets)
    levels = pd.DataFrame(index=cal, columns=returns.columns, dtype=float)
    for c in returns:
        first = returns[c].first_valid_index()
        start = cal[cal.get_loc(first) - 1]
        lv = (1 + returns[c].loc[first:]).cumprod()
        levels[c] = pd.concat([pd.Series([1.0], index=[start]), lv]).reindex(cal)

    cash = uni.get("cash_asset")
    daily_fallback = (1 + FALLBACK_CASH_RATE) ** (1 / SESSIONS_PER_YEAR) - 1
    rf = returns[cash].fillna(daily_fallback) if cash else pd.Series(daily_fallback, index=cal)

    adv = {}
    for a in uni["assets"]:
        if a.get("etf"):
            e = etf[a["etf"]]
            last_year = e[e.index >= e.index.max() - pd.Timedelta(days=365)]
            adv[a["id"]] = float((last_year["close"] * last_year["volume"]).median() / 1e7)

    reference = {}
    for name, b in cfg.get("benchmarks", {}).items():
        if isinstance(b, dict):
            reference[name] = _load(nse_path(b["index"]), INDEX_SCHEMA).set_index("date")["close"].reindex(cal)

    return Panel(cal, returns, levels, rf, ohlc, dy_fallback, ffilled, etf, etf_spikes, pd.Series(adv), reference)
