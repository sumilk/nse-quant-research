"""Download and cache index, dividend-yield and ETF history, shared by all strategies.

Files are keyed by source and instrument (not by strategy), so two strategies
that use NIFTY BANK read the same download:
    data/raw/nse_<index-slug>.parquet      NSE index OHLC
    data/raw/nsedy_<index-slug>.parquet    NSE dividend yield
    data/raw/etf_<TICKER>.parquet          Yahoo ETF prices

NSE's historicalOR/indicesHistory API returns at most 70 rows per call, so
requests go in 90-day windows and any window that hits the cap is split in two.
Dividend yields (published from ~2015) turn price indices into approximate
total-return indices. Gold and silver have no NSE index, so ETF prices are used.
"""

from __future__ import annotations

import re
import time
from datetime import date, timedelta
from urllib.parse import quote

import pandas as pd
import requests
import yfinance as yf

from strategylab.config import RAW, list_strategies, load_config

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/128 Safari/537.36"
WINDOW_DAYS = 90
ROW_CAP = 70
PAUSE_S = 0.35
DY_START = date(2015, 1, 1)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def nse_path(index: str):
    return RAW / f"nse_{slug(index)}.parquet"


def nsedy_path(index: str):
    return RAW / f"nsedy_{slug(index)}.parquet"


def etf_path(ticker: str):
    return RAW / f"etf_{ticker}.parquet"


class NSEClient:
    PAGE = "https://www.nseindia.com/reports-indices-historical-index-data"

    def __init__(self) -> None:
        self.s = requests.Session()
        self._warm()

    def _warm(self) -> None:
        self.s.get(self.PAGE, headers={"User-Agent": UA}, timeout=30)

    def _get(self, endpoint: str, index: str, start: date, end: date) -> list[dict]:
        url = (f"https://www.nseindia.com/api/historicalOR/{endpoint}?indexType={quote(index)}"
               f"&from={start:%d-%m-%Y}&to={end:%d-%m-%Y}")
        headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Referer": self.PAGE}
        for attempt in range(5):
            try:
                r = self.s.get(url, headers=headers, timeout=30)
                if r.status_code == 200:
                    return r.json().get("data", [])
            except (requests.RequestException, ValueError):
                pass
            time.sleep(2 * (attempt + 1))
            self._warm()
        raise RuntimeError(f"NSE request failed after retries: {url}")

    def _window(self, endpoint: str, index: str, lo: date, hi: date) -> list[dict]:
        rows = self._get(endpoint, index, lo, hi)
        time.sleep(PAUSE_S)
        if len(rows) < ROW_CAP:
            return rows
        if lo == hi:
            raise RuntimeError(f"{index} {lo}: {len(rows)} rows for a single day")
        mid = lo + (hi - lo) // 2
        return self._window(endpoint, index, lo, mid) + self._window(endpoint, index, mid + timedelta(days=1), hi)

    def _chunked(self, endpoint: str, index: str, start: date, end: date) -> list[dict]:
        rows, lo = [], start
        while lo <= end:
            hi = min(lo + timedelta(days=WINDOW_DAYS - 1), end)
            rows += self._window(endpoint, index, lo, hi)
            lo = hi + timedelta(days=1)
        return rows

    def index_history(self, index: str, start: date, end: date) -> pd.DataFrame:
        df = pd.DataFrame(self._chunked("indicesHistory", index, start, end))
        if df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
        return pd.DataFrame({
            "date": pd.to_datetime(df["EOD_TIMESTAMP"], format="%d-%b-%Y"),
            "open": df["EOD_OPEN_INDEX_VAL"], "high": df["EOD_HIGH_INDEX_VAL"],
            "low": df["EOD_LOW_INDEX_VAL"], "close": df["EOD_CLOSE_INDEX_VAL"],
        }).astype({c: float for c in ["open", "high", "low", "close"]})

    def dividend_yield(self, index: str, start: date, end: date) -> pd.DataFrame:
        df = pd.DataFrame(self._chunked("indicesYield", index, start, end))
        if df.empty:
            return pd.DataFrame(columns=["date", "dy_pct"])
        return pd.DataFrame({"date": pd.to_datetime(df["IY_DT"], format="%d-%b-%Y"),
                             "dy_pct": pd.to_numeric(df["IY_DY"], errors="coerce")})


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    # Chunk edges occasionally repeat a session; keep the last print.
    return df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def requirements(strategy_ids: list[str]) -> tuple[dict[str, tuple[date, bool]], dict[str, date]]:
    """Union of instruments needed: NSE index -> (start, needs dividend yield); ETF ticker -> start."""
    nse, etfs = {}, {}
    for sid in strategy_ids:
        cfg = load_config(sid)
        start = date.fromisoformat(cfg["periods"]["data_start"])
        items = list(cfg["universe"]["assets"]) + [b for b in cfg.get("benchmarks", {}).values() if isinstance(b, dict)]
        for a in items:
            if a.get("source") in ("nse", "nse_tr"):
                prev = nse.get(a["index"], (start, False))
                nse[a["index"]] = (min(prev[0], start), prev[1] or a["source"] == "nse")
            if a.get("etf"):
                etfs[a["etf"]] = min(etfs.get(a["etf"], start), start)
    return nse, etfs


def fetch(strategy_ids: list[str] | None = None, refresh: bool = False, end: date | None = None) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    end = end or date.today()
    nse, etfs = requirements(strategy_ids or list_strategies())
    client = None
    for index, (start, needs_dy) in sorted(nse.items()):
        jobs = [("index", nse_path(index))] + ([("dy", nsedy_path(index))] if needs_dy else [])
        for kind, path in jobs:
            if path.exists() and not refresh:
                continue
            client = client or NSEClient()
            print(f"[nse] {index} ({kind})", flush=True)
            df = (client.index_history(index, start, end) if kind == "index"
                  else client.dividend_yield(index, max(start, DY_START), end))
            _finalize(df).to_parquet(path)

    todo = [t for t in etfs if refresh or not etf_path(t).exists()]
    if todo:
        print(f"[yahoo] {len(todo)} ETFs", flush=True)
        start = min(etfs[t] for t in todo)
        raw = yf.download([f"{t}.NS" for t in todo], start=str(start), end=str(end + timedelta(days=1)),
                          auto_adjust=False, progress=False, group_by="ticker")
        for t in todo:
            x = raw[f"{t}.NS"].dropna(subset=["Close"]).reset_index()
            if x.empty:
                raise RuntimeError(f"Yahoo returned no rows for {t}.NS")
            pd.DataFrame({
                "date": pd.to_datetime(x["Date"]).dt.tz_localize(None),
                "open": x["Open"], "high": x["High"], "low": x["Low"],
                "close": x["Close"], "adj_close": x["Adj Close"], "volume": x["Volume"].astype(float),
            }).to_parquet(etf_path(t))
