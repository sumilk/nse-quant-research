"""Build a strategy's daily total-return panel on the NSE trading calendar.

Calendar: the sessions of the strategy's calendar_master index (calendar-ops: one
exchange-specific calendar, never generic business days).

Total return for NSE price indices = price return + dividend accrual. The
accrual for session t uses the dividend yield published for session t-1
(point-in-time: today's yield is only known after today's close). Before NSE
published yields (~2015), zero accrual is used and disclosed as a price-only
proxy. Future yields are never backfilled.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from strategylab.config import ROOT
from strategylab.data.fetch import amfi_path, etf_path, nse_path, nsedy_path

SESSIONS_PER_YEAR = 252
MAX_FFILL_SESSIONS = 2
FALLBACK_CASH_RATE = 0.06  # risk-free rate only, before a cash asset has history
# Reviewed source corrections, not automatic filters chosen from future prices.
BAD_ETF_DATES = {ticker: pd.to_datetime(["2019-12-19", "2019-12-20"])
                 for ticker in ("NIFTYBEES", "BANKBEES", "PSUBNKBEES", "GOLDBEES")}
NAV_REBASES = {100868: [(pd.Timestamp("2015-08-30"), 100.0)]}

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


def _adjust_rebases(px: pd.DataFrame, factor: float = 3.0) -> tuple[pd.DataFrame, list]:
    """Undo persistent level jumps (unit splits, face-value changes) by rescaling earlier history.

    Run after _drop_spikes: a jump that is still there is permanent, e.g. the HDFC
    Liquid Fund NAV moving from 28.52 to 2852.85 on 2015-08-30. Returns the
    adjusted frame and (date, ratio) for each adjustment.
    """
    px = px.copy()
    ratio = px["close"] / px["close"].shift(1)
    events = [(d, float(r)) for d, r in ratio[(ratio > factor) | (ratio < 1 / factor)].items()]
    price_cols = [c for c in ("open", "high", "low", "close", "adj_close") if c in px]
    for d, r in events:
        px.loc[px.index < d, price_cols] *= r
    return px, events


def _load(path, schema: dict, cutoff=None) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"Missing {path.relative_to(ROOT)}. Run `uv run lab fetch` first.")
    df = pd.read_parquet(path)
    if cutoff is not None:
        df = df.loc[pd.to_datetime(df["date"]) <= pd.Timestamp(cutoff)].copy()
    for col, dtype in schema.items():
        if col not in df.columns:
            raise ValueError(f"{path.name}: missing column {col}")
        if len(df):
            df[col] = df[col].astype(dtype)
    if df["date"].duplicated().any():
        raise ValueError(f"{path.name}: duplicate dates")
    return df.sort_values("date")


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
    etf_spikes: dict[str, list]    # source series -> dates of removed one-off prints
    rebases: dict[str, list]       # source series -> (date, ratio) of persistent jumps undone
    etf_adv_cr: pd.Series          # trailing ADV at the cutoff, descriptive only, Rs crore
    reference: dict[str, pd.Series]  # reference benchmarks (price series, no costs or tax)
    observed: pd.DataFrame = field(default_factory=pd.DataFrame)
    adv_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    gaps: dict[str, list] = field(default_factory=dict)
    mode: str = "index_proxy"
    cutoff: pd.Timestamp | None = None


def build_panel(cfg: dict, *, cutoff=None, mode: str | None = None) -> Panel:
    """Clip every source before cleaning; default to the development cutoff.

    index_proxy is a hypothetical index allocation experiment. etf uses only
    observed ETF adjusted closes and trailing ETF liquidity, never index backfills.
    """
    cutoff = pd.Timestamp(cutoff if cutoff is not None else cfg["periods"]["dev_end"])
    mode = mode or cfg.get("data", {}).get("mode", "index_proxy")
    if mode not in ("index_proxy", "etf"):
        raise ValueError(f"Unknown data mode: {mode}")
    def load(path, schema):
        return _load(path, schema, cutoff)
    uni = cfg["universe"]
    master_index = next(a["index"] for a in uni["assets"] if a["id"] == uni["calendar_master"])
    master = load(nse_path(master_index), INDEX_SCHEMA)
    cal = pd.DatetimeIndex(master["date"]).sort_values()
    cal = cal[cal >= pd.Timestamp(cfg["periods"]["data_start"])]
    if cal.empty:
        raise ValueError("No calendar sessions on or before the source cutoff")

    etf, etf_spikes, rebases = {}, {}, {}
    for a in uni["assets"]:
        if a.get("etf"):
            ticker = a["etf"]
            e = load(etf_path(ticker), ETF_SCHEMA).set_index("date")
            bad = e.index.intersection(BAD_ETF_DATES.get(ticker, pd.DatetimeIndex([])))
            etf_spikes[ticker] = list(bad)
            # Yahoo adjusted closes already contain corporate-action adjustments.
            # Do not re-adjust them from later raw-price jumps.
            etf[ticker], rebases[ticker] = e.drop(index=bad), []
    rets, ohlc, dy_fallback, ffilled = {}, {}, {}, {}
    observed, gaps = {}, {}

    for a in uni["assets"]:
        aid = a["id"]
        if mode == "etf":
            close = etf[a["etf"]]["adj_close"]
        elif a["source"] in ("nse", "nse_tr"):
            px = load(nse_path(a["index"]), INDEX_SCHEMA).set_index("date")
            ohlc[aid] = px
            close = px["close"]
        elif a["source"] == "yahoo":
            close = etf[a["etf"]]["adj_close"]
        elif a["source"] == "amfi":
            key = f"AMFI {a['scheme']}"
            scheme = int(a["scheme"])
            nav = load(amfi_path(scheme), {"date": "datetime64[ns]", "close": "float64"}).set_index("date")
            etf_spikes[key], rebases[key] = [], []
            for when, factor in NAV_REBASES.get(scheme, []):
                if when <= cutoff:
                    nav.loc[nav.index < when, "close"] *= factor
                    rebases[key].append((when, factor))
            close = nav["close"]
        else:
            raise ValueError(f"{aid}: unknown source {a['source']}")

        on_cal = close.reindex(cal)
        observed[aid] = on_cal.notna() & np.isfinite(on_cal) & (on_cal > 0)
        first = on_cal.first_valid_index()
        if first is None:
            rets[aid] = pd.Series(np.nan, index=cal)
            ffilled[aid], gaps[aid] = 0, []
            continue
        live = on_cal.loc[first:]
        ffilled[aid] = int(live.isna().sum())
        live = live.ffill(limit=MAX_FFILL_SESSIONS)
        # Never stitch a return across an unvalued gap. The data gate will stop
        # on these dates; they must be repaired from a source, not interpolated.
        gaps[aid] = list(live.index[live.isna() | ~np.isfinite(live) | (live <= 0)])
        r = live.pct_change(fill_method=None)

        if mode == "index_proxy" and a["source"] == "nse":
            dy = load(nsedy_path(a["index"]), DY_SCHEMA)
            if dy.empty:
                dy_series = pd.Series(0.0, index=live.index)
                dy_fallback[aid] = (None, np.nan)
            else:
                dy = dy.set_index("date")["dy_pct"].sort_index()
                first_dy = dy.index[0]
                # No future dividend-yield backfill. Before publication, this
                # proxy has price returns only (explicitly disclosed in reports).
                fill = 0.0
                dy_series = dy.reindex(live.index, method="ffill").fillna(fill)
                dy_fallback[aid] = (first_dy, fill)
            # Yield known at the close of t-1 accrues over session t.
            r = r + dy_series.shift(1) / 100 / SESSIONS_PER_YEAR
        rets[aid] = r.reindex(cal)

    returns = pd.DataFrame(rets)
    levels = pd.DataFrame(index=cal, columns=returns.columns, dtype=float)
    for c in returns:
        first = returns[c].first_valid_index()
        if first is None:
            continue
        start = cal[cal.get_loc(first) - 1]
        lv = (1 + returns[c].loc[first:]).cumprod()
        levels[c] = pd.concat([pd.Series([1.0], index=[start]), lv]).reindex(cal)

    cash = uni.get("cash_asset")
    daily_fallback = (1 + FALLBACK_CASH_RATE) ** (1 / SESSIONS_PER_YEAR) - 1
    rf = returns[cash].fillna(daily_fallback) if cash else pd.Series(daily_fallback, index=cal)

    adv, adv_hist = {}, {}
    for a in uni["assets"]:
        if a.get("etf"):
            e = etf[a["etf"]]
            value = (e["close"] * e["volume"]).reindex(cal)
            # The order-date estimate sees only prior sessions. A missing/zero
            # quote is not a source of executable liquidity.
            valid = value.where(value > 0)
            trailing = valid.rolling(63, min_periods=21).median().shift(1)
            adv_hist[a["id"]] = trailing
            adv[a["id"]] = float(trailing.iloc[-1] / 1e7) if len(trailing) else np.nan

    reference = {}
    for name, b in cfg.get("benchmarks", {}).items():
        if isinstance(b, dict):
            reference[name] = load(nse_path(b["index"]), INDEX_SCHEMA).set_index("date")["close"].reindex(cal)

    return Panel(cal, returns, levels, rf, ohlc, dy_fallback, ffilled, etf, etf_spikes, rebases, pd.Series(adv),
                 reference, pd.DataFrame(observed), pd.DataFrame(adv_hist), gaps, mode, cutoff)
