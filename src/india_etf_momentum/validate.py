"""Data gate (ml4t-validate-data): OHLC consistency, duplicates, gaps, stale runs,
extreme moves, and index-vs-ETF tracking. Writes results/data_validation.md."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ROOT, load_config
from .panel import Panel

EXTREME_INDEX_MOVE = 0.20
STALE_RUN = 5
MIN_TRACKING_CORR = 0.95


def _stale_runs(close: pd.Series) -> int:
    same = close.diff().eq(0)
    run = same.groupby((~same).cumsum()).cumsum()
    return int((run >= STALE_RUN).sum())


def validate(p: Panel) -> tuple[bool, str]:
    cfg = load_config()
    rows, extremes, failures = [], [], []
    for a in cfg["universe"]["assets"]:
        aid = a["id"]
        r = p.returns[aid].dropna()
        lv = p.levels[aid].dropna()
        row = {"asset": aid, "first": lv.index[0].date(), "last": lv.index[-1].date(), "sessions": len(lv),
               "ffilled": p.ffilled[aid]}
        if aid in p.index_ohlc:
            o = p.index_ohlc[aid]
            o = o[o.index >= p.calendar[0]]
            bad_hl = int((o["high"] < o["low"]).sum())
            bad_c = int(((o["close"] > o["high"] * 1.0001) | (o["close"] < o["low"] * 0.9999)).sum())
            row.update(high_lt_low=bad_hl, close_outside_hl=bad_c, stale_runs=_stale_runs(o["close"]))
            off_cal = int((~o.index.isin(p.calendar)).sum())
            row["off_calendar"] = off_cal
        else:
            e = p.etf[p.etf["etf"] == a["etf"]].set_index("date")["adj_close"]
            row.update(high_lt_low=0, close_outside_hl=0, stale_runs=_stale_runs(e),
                       off_calendar=int((~e.index.isin(p.calendar)).sum()))
        big = r[r.abs() > EXTREME_INDEX_MOVE]
        row["extreme_moves"] = len(big)
        extremes += [(aid, d.date(), f"{v:+.1%}") for d, v in big.items()]
        fb = p.dy_fallback.get(aid)
        row["dy_from"] = fb[0].date() if fb and fb[0] is not None else ("n/a" if fb is None else "missing")
        row["dy_fill_pct"] = round(fb[1], 2) if fb and fb[0] is not None else None

        # Index vs ETF tracking on the overlap (weekly, to avoid close-time asynchrony).
        if a["source"] != "yahoo" and aid != "CASH":
            e = p.etf[p.etf["etf"] == a["etf"]].set_index("date")["adj_close"].reindex(p.calendar).ffill(limit=2)
            both = pd.concat([p.levels[aid], e], axis=1, keys=["idx", "etf"]).dropna()
            wk = both.resample("W-FRI").last().pct_change().dropna()
            if len(wk) > 26:
                corr = wk["idx"].corr(wk["etf"])
                yrs = (both.index[-1] - both.index[0]).days / 365.25
                td = ((both["etf"].iloc[-1] / both["etf"].iloc[0]) ** (1 / yrs)
                      - (both["idx"].iloc[-1] / both["idx"].iloc[0]) ** (1 / yrs))
                row.update(etf_weekly_corr=round(corr, 3), etf_tracking_diff_pa=f"{td:+.2%}",
                           etf_overlap_from=both.index[0].date())
                if corr < MIN_TRACKING_CORR:
                    failures.append(f"{aid}: index-ETF weekly correlation {corr:.3f} < {MIN_TRACKING_CORR}")
        row["etf_adv_cr"] = round(float(p.etf_adv_cr.get(aid, np.nan)), 2)
        if row["high_lt_low"] or row["close_outside_hl"]:
            failures.append(f"{aid}: OHLC inconsistencies")
        if row["stale_runs"]:
            failures.append(f"{aid}: {row['stale_runs']} sessions inside stale runs of >= {STALE_RUN}")
        rows.append(row)

    table = pd.DataFrame(rows)
    ok = not failures
    lines = ["# Data validation", "", f"Gate: **{'PASS' if ok else 'FAIL'}**", "",
             f"Calendar: {len(p.calendar)} NSE sessions, {p.calendar[0].date()} to {p.calendar[-1].date()}"
             " (NIFTY 50 trading days).", "",
             table.to_markdown(index=False), "",
             "## Extreme daily moves (> 20%)", ""]
    lines += [f"- {a} {d}: {v}" for a, d, v in extremes] or ["- none"]
    lines += ["", "## Failures", ""] + ([f"- {f}" for f in failures] or ["- none"])
    lines += ["", "## Notes", "",
              "- `dy_from`: first session with a published NSE dividend yield. Earlier sessions use "
              "`dy_fill_pct`, the median yield over the first published year (approximation).",
              "- `ffilled`: sessions missing after the first price and carried forward (max 2 per gap).",
              "- `off_calendar`: source rows on dates that are not NIFTY 50 sessions (dropped).",
              "- ETF ADV is the median daily traded value over the last year: it sets spread tiers, "
              "impact and capacity, and is applied to all history (a known approximation).",
              ]
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / "data_validation.md").write_text("\n".join(lines) + "\n")
    return ok, "\n".join(lines)
