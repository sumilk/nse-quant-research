"""Download and cache index, dividend-yield and ETF history.

NSE index prices come from the historicalOR/indicesHistory API, which returns at
most 70 rows per call, so requests are chunked into 90-day windows. Dividend
yields (published from ~2016) turn the price index into an approximate total
return index. Gold and silver have no NSE index, so their ETF prices are used.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
import yfinance as yf

from .config import ROOT, load_config

RAW = ROOT / "data" / "raw"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/128 Safari/537.36"
WINDOW_DAYS = 90
PAUSE_S = 0.35


class NSEClient:
    def __init__(self) -> None:
        self.s = requests.Session()
        self._warm("https://www.nseindia.com/reports-indices-historical-index-data")

    def _warm(self, page: str) -> None:
        self.s.get(page, headers={"User-Agent": UA}, timeout=30)

    def _get(self, endpoint: str, index: str, start: date, end: date) -> list[dict]:
        url = (
            f"https://www.nseindia.com/api/historicalOR/{endpoint}?indexType={quote(index)}"
            f"&from={start:%d-%m-%Y}&to={end:%d-%m-%Y}"
        )
        headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                   "Referer": "https://www.nseindia.com/reports-indices-historical-index-data"}
        for attempt in range(5):
            try:
                r = self.s.get(url, headers=headers, timeout=30)
                if r.status_code == 200:
                    return r.json().get("data", [])
            except (requests.RequestException, ValueError):
                pass
            time.sleep(2 * (attempt + 1))
            self._warm("https://www.nseindia.com/reports-indices-historical-index-data")
        raise RuntimeError(f"NSE request failed after retries: {url}")

    def _chunked(self, endpoint: str, index: str, start: date, end: date) -> list[dict]:
        rows, lo = [], start
        while lo <= end:
            hi = min(lo + timedelta(days=WINDOW_DAYS - 1), end)
            chunk = self._get(endpoint, index, lo, hi)
            if len(chunk) >= 70:
                raise RuntimeError(f"{index} {lo}..{hi}: hit the 70-row cap, shrink WINDOW_DAYS")
            rows += chunk
            lo = hi + timedelta(days=1)
            time.sleep(PAUSE_S)
        return rows

    def index_history(self, index: str, start: date, end: date) -> pd.DataFrame:
        rows = self._chunked("indicesHistory", index, start, end)
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df = pd.DataFrame({
            "date": pd.to_datetime(df["EOD_TIMESTAMP"], format="%d-%b-%Y"),
            "open": df["EOD_OPEN_INDEX_VAL"], "high": df["EOD_HIGH_INDEX_VAL"],
            "low": df["EOD_LOW_INDEX_VAL"], "close": df["EOD_CLOSE_INDEX_VAL"],
        })
        return df.astype({c: float for c in ["open", "high", "low", "close"]})

    def dividend_yield(self, index: str, start: date, end: date) -> pd.DataFrame:
        rows = self._chunked("indicesYield", index, start, end)
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        return pd.DataFrame({
            "date": pd.to_datetime(df["IY_DT"], format="%d-%b-%Y"),
            "dy_pct": pd.to_numeric(df["IY_DY"], errors="coerce"),
        })


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    # NSE returns duplicates at chunk edges occasionally; keep the last print.
    return df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_all(refresh: bool = False, end: date | None = None) -> None:
    cfg = load_config()
    RAW.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(cfg["periods"]["data_start"])
    end = end or date.today()
    client = NSEClient()

    nse_jobs = [(a["id"], a["index"], a["source"]) for a in cfg["universe"]["assets"]
                if a["source"] in ("nse", "nse_tr")]
    nse_jobs.append(("MOMENTUM30", cfg["benchmarks"]["momentum30"]["index"], "nse"))

    for asset_id, index, source in nse_jobs:
        px_path, dy_path = RAW / f"{asset_id}_index.parquet", RAW / f"{asset_id}_dy.parquet"
        if refresh or not px_path.exists():
            print(f"[nse] {asset_id:12s} {index}")
            _finalize(client.index_history(index, start, end)).to_parquet(px_path)
        if source == "nse" and (refresh or not dy_path.exists()):
            print(f"[nse] {asset_id:12s} dividend yield")
            dy = client.dividend_yield(index, date(2015, 1, 1), end)
            (_finalize(dy) if not dy.empty else dy).to_parquet(dy_path)

    etfs = [a["etf"] for a in cfg["universe"]["assets"]]
    etf_path = RAW / "etf_prices.parquet"
    if refresh or not etf_path.exists():
        print(f"[yahoo] {len(etfs)} ETFs")
        raw = yf.download([f"{t}.NS" for t in etfs], start=str(start), end=str(end + timedelta(days=1)),
                          auto_adjust=False, progress=False, group_by="ticker")
        frames = []
        for t in etfs:
            x = raw[f"{t}.NS"].dropna(subset=["Close"]).reset_index()
            frames.append(pd.DataFrame({
                "etf": t, "date": pd.to_datetime(x["Date"]).dt.tz_localize(None),
                "open": x["Open"], "high": x["High"], "low": x["Low"],
                "close": x["Close"], "adj_close": x["Adj Close"], "volume": x["Volume"],
            }))
        pd.concat(frames, ignore_index=True).to_parquet(etf_path)


if __name__ == "__main__":
    fetch_all()
