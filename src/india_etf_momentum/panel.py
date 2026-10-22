"""Build the daily total-return panel on the NSE trading calendar.

Calendar: the sessions of the NIFTY 50 index (calendar-ops: one exchange-specific
calendar; never generic business days).

Total return for NSE price indices = price return + dividend accrual. The
accrual for session t uses the dividend yield published for session t-1
(point-in-time: today's yield is only known after today's close). Before NSE
published yields (~2016) the asset's median yield over its first published
year is used and flagged in the validation report.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import ROOT, load_config
from .data import RAW

SESSIONS_PER_YEAR = 252
MAX_FFILL_SESSIONS = 2
FALLBACK_CASH_RATE = 0.06  # used for the risk-free rate only before the 1D rate index exists

INDEX_SCHEMA = {"date": "datetime64[ns]", "open": "float64", "high": "float64",
                "low": "float64", "close": "float64"}
DY_SCHEMA = {"date": "datetime64[ns]", "dy_pct": "float64"}
ETF_SCHEMA = {"etf": "object", "date": "datetime64[ns]", "close": "float64",
              "adj_close": "float64", "volume": "float64"}


def _load(path, schema: dict) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if df.empty:
        return df
    for col, dtype in schema.items():
        assert col in df.columns, f"{path.name}: missing column {col}"
        if dtype == "object":
            df[col] = df[col].astype(str)
        else:
            df[col] = df[col].astype(dtype)
    keys = ["etf", "date"] if "etf" in schema else ["date"]
    assert not df.duplicated(keys).any(), f"{path.name}: duplicate dates"
    return df


@dataclass
class Panel:
    calendar: pd.DatetimeIndex
    returns: pd.DataFrame          # daily total returns, sessions x asset (NaN before first price)
    levels: pd.DataFrame           # total-return levels (cumprod of returns), NaN before start
    rf: pd.Series                  # daily risk-free return (1D rate index, fallback constant)
    index_ohlc: dict[str, pd.DataFrame]
    dy_fallback: dict[str, tuple[pd.Timestamp | None, float]]
    ffilled: dict[str, int]
    etf: pd.DataFrame              # raw ETF prices (long)
    etf_adv_cr: pd.Series          # median daily traded value, last 250 sessions, Rs crore
    momentum30: pd.Series          # NIFTY200 Momentum 30 price index (reference benchmark)


def build_panel() -> Panel:
    cfg = load_config()
    master = _load(RAW / "NIFTY50_index.parquet", INDEX_SCHEMA)
    cal = pd.DatetimeIndex(master["date"]).sort_values()
    cal = cal[cal >= pd.Timestamp(cfg["periods"]["data_start"])]

    etf = _load(RAW / "etf_prices.parquet", ETF_SCHEMA)
    rets, ohlc, dy_fallback, ffilled = {}, {}, {}, {}

    for a in cfg["universe"]["assets"]:
        aid = a["id"]
        if a["source"] in ("nse", "nse_tr"):
            px = _load(RAW / f"{aid}_index.parquet", INDEX_SCHEMA).set_index("date")
            ohlc[aid] = px
            close = px["close"]
        else:
            e = etf[etf["etf"] == a["etf"]].set_index("date")
            close = e["adj_close"]

        on_cal = close.reindex(cal)
        first = on_cal.first_valid_index()
        if first is None:
            raise ValueError(f"{aid}: no prices on the NSE calendar")
        live = on_cal.loc[first:]
        ffilled[aid] = int(live.isna().sum())
        live = live.ffill(limit=MAX_FFILL_SESSIONS)
        r = live.pct_change()

        if a["source"] == "nse":
            dy = _load(RAW / f"{aid}_dy.parquet", DY_SCHEMA)
            if dy.empty:
                dy_series = pd.Series(np.nan, index=live.index)
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
    # Level exists from the first price onward (first session has NaN return but a price).
    levels = pd.DataFrame(index=cal, columns=returns.columns, dtype=float)
    for c in returns:
        first = returns[c].first_valid_index()
        start = cal[cal.get_loc(first) - 1]
        lv = (1 + returns[c].loc[first:]).cumprod()
        levels[c] = pd.concat([pd.Series([1.0], index=[start]), lv]).reindex(cal)

    rf = returns["CASH"].copy()
    rf = rf.fillna((1 + FALLBACK_CASH_RATE) ** (1 / SESSIONS_PER_YEAR) - 1)

    last_year = etf[etf["date"] >= etf["date"].max() - pd.Timedelta(days=365)]
    adv = (last_year.assign(tv=last_year["close"] * last_year["volume"])
           .groupby("etf")["tv"].median() / 1e7)
    etf_to_id = {a["etf"]: a["id"] for a in cfg["universe"]["assets"]}
    adv.index = adv.index.map(etf_to_id)

    m30 = _load(RAW / "MOMENTUM30_index.parquet", INDEX_SCHEMA).set_index("date")["close"]

    return Panel(cal, returns, levels, rf, ohlc, dy_fallback, ffilled, etf, adv, m30.reindex(cal))


def save_panel(p: Panel) -> None:
    out = ROOT / "data" / "panel"
    out.mkdir(parents=True, exist_ok=True)
    p.returns.to_parquet(out / "returns.parquet")
    p.levels.to_parquet(out / "levels.parquet")
