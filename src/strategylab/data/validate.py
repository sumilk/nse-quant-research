"""Data gate (ml4t-validate-data): OHLC consistency, duplicates, gaps, stale runs,
extreme moves, and index-vs-ETF tracking. Writes <strategy>/results/data_validation.md."""

from __future__ import annotations

import numpy as np
import pandas as pd

from pathlib import Path

from strategylab.data.panel import Panel

EXTREME_INDEX_MOVE = 0.20
STALE_RUN = 5
MIN_TRACKING_CORR = 0.95


def _stale_runs(close: pd.Series) -> int:
    same = close.diff().eq(0)
    run = same.groupby((~same).cumsum()).cumsum()
    return int((run >= STALE_RUN).sum())


def validate(p: Panel, cfg: dict, out: Path) -> tuple[bool, str]:
    rows, extremes, failures = [], [], []
    notes = cfg.get("data_notes", {})
    explained = {(e["asset"], str(e["date"])): e["reason"] for e in notes.get("explained_moves", [])}
    waivers = {(w["asset"], w["check"]): w["reason"] for w in notes.get("waivers", [])}
    waived = []

    def fail(asset: str, check: str, msg: str) -> None:
        if (asset, check) in waivers:
            waived.append(f"{msg} — waived: {waivers[(asset, check)]}")
        else:
            failures.append(msg)
    for a in cfg["universe"]["assets"]:
        aid = a["id"]
        r = p.returns[aid].dropna()
        lv = p.levels[aid].dropna()
        missing = p.gaps.get(aid, [])
        if missing:
            failures.append(f"{aid}: unresolved valuation gaps: " + ", ".join(str(d.date()) for d in missing))
        row = {"asset": aid, "first": lv.index[0].date() if len(lv) else None,
               "last": lv.index[-1].date() if len(lv) else None, "sessions": len(lv),
               "unresolved_gaps": len(missing),
               "ffilled": p.ffilled[aid]}
        if aid in p.index_ohlc:
            o = p.index_ohlc[aid]
            o = o[o.index >= p.calendar[0]]
            # Total-return bond/rate indices publish only a close (OHLC NaN or 0): check rows that have a range.
            o = o[(o["high"] > 0) & (o["low"] > 0)]
            bad_hl = int((o["high"] < o["low"]).sum())
            bad_c = int(((o["close"] > o["high"] * 1.0001) | (o["close"] < o["low"] * 0.9999)).sum())
            row.update(high_lt_low=bad_hl, close_outside_hl=bad_c, stale_runs=_stale_runs(o["close"]))
            off_cal = int((~o.index.isin(p.calendar)).sum())
            row["off_calendar"] = off_cal
        else:
            # Check the series the panel actually uses (ETF price, or fund NAV for amfi sources).
            e = p.levels[aid].dropna() if a["source"] == "amfi" else p.etf[a["etf"]]["adj_close"]
            row.update(high_lt_low=0, close_outside_hl=0, stale_runs=_stale_runs(e),
                       off_calendar=int((~e.index.isin(p.calendar)).sum()))
        big = r[r.abs() > EXTREME_INDEX_MOVE]
        row["extreme_moves"] = len(big)
        for d, v in big.items():
            why = explained.get((aid, str(d.date())))
            extremes.append((aid, d.date(), f"{v:+.1%}", why))
            if why is None:
                failures.append(f"{aid} {d.date()}: unexplained {v:+.1%} move")
        if a.get("etf"):
            row["etf_spikes_removed"] = len(p.etf_spikes.get(a["etf"], []))
        fb = p.dy_fallback.get(aid)
        row["dy_from"] = fb[0].date() if fb and fb[0] is not None else ("n/a" if fb is None else "missing")
        row["dy_fill_pct"] = round(fb[1], 2) if fb and fb[0] is not None else None

        # Index vs ETF tracking on the overlap (weekly, to avoid close-time asynchrony).
        if p.mode == "index_proxy" and a["source"] != "yahoo" and a.get("etf") and aid != cfg["universe"].get("cash_asset"):
            e = p.etf[a["etf"]]["adj_close"].reindex(p.calendar).ffill(limit=2)
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
                    fail(aid, "tracking", f"{aid}: index-ETF weekly correlation {corr:.3f} < {MIN_TRACKING_CORR}")
        row["etf_adv_cr"] = round(float(p.etf_adv_cr.get(aid, np.nan)), 2)
        if row["high_lt_low"] or row["close_outside_hl"]:
            fail(aid, "ohlc", f"{aid}: OHLC inconsistencies")
        if row["stale_runs"]:
            fail(aid, "stale", f"{aid}: {row['stale_runs']} sessions inside stale runs of >= {STALE_RUN}")
        rows.append(row)

    table = pd.DataFrame(rows)
    ok = not failures
    lines = ["# Data validation", "", f"Mode: **{p.mode}**. Source cutoff: **{p.cutoff}**.", "", f"Gate: **{'PASS' if ok else 'FAIL'}**", "",
             f"Calendar: {len(p.calendar)} NSE sessions, {p.calendar[0].date()} to {p.calendar[-1].date()}"
             f" ({cfg['universe']['calendar_master']} trading days).", "",
             table.to_markdown(index=False), "",
             "## Extreme daily moves (> 20%)", ""]
    lines += [f"- {a} {d}: {v} — {why or '**UNEXPLAINED**'}" for a, d, v, why in extremes] or ["- none"]
    spikes = {t: ds for t, ds in p.etf_spikes.items() if ds}
    lines += ["", "## Reviewed bad prints excluded (fixed dates; no future-median filter)", ""]
    lines += [f"- {t}: {', '.join(str(d.date()) for d in ds)}" for t, ds in spikes.items()] or ["- none"]
    rebased = {t: ev for t, ev in p.rebases.items() if ev}
    lines += ["", "## Persistent level jumps undone (splits, face-value changes; earlier history rescaled)", ""]
    lines += [f"- {t}: " + ", ".join(f"{d.date()} x{r:,.2f}" for d, r in ev) for t, ev in rebased.items()] or ["- none"]
    lines += ["", "## Failures", ""] + ([f"- {f}" for f in failures] or ["- none"])
    lines += ["", "## Waived (documented in config data_notes.waivers)", ""] + ([f"- {w}" for w in waived] or ["- none"])
    lines += ["", "## Notes", "",
              "- `dy_from`: first session with a published NSE dividend yield. Earlier sessions use "
              "zero accrual; no yield from future observations is backfilled (price-only approximation).",
              "- `ffilled`: sessions missing after the first price and carried forward (max 2 per gap).",
              "- `off_calendar`: source rows on dates that are not NIFTY 50 sessions (dropped).",
              "- ETF ADV shown here is the trailing 63-session median at the source cutoff; "
              "ETF-mode trade costs use the lagged estimate for each individual trade date.",
              "- Index-proxy mode uses a declared fixed liquidity assumption, not future ETF liquidity.",
              "- Missing/stale quotes are never execution prices. Unresolved valuation gaps are fatal.",
              ]
    out.mkdir(parents=True, exist_ok=True)
    (out / "data_validation.md").write_text("\n".join(lines) + "\n")
    return ok, "\n".join(lines)
