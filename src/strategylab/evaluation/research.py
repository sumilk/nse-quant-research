"""Development-period research: data gate, signal gate, the trial grid, selection
statistics, cost sensitivity, capacity, and the pre-registered gate table.

Works for any strategy whose config names a registered model (see models/).
What is tested is driven by the strategy's config:
  evaluation.signal_test       cross_sectional (rank IC) | time_series (on-minus-off spread)
  evaluation.selection_series  active_vs_ew | excess_vs_cash  (series for DSR, PBO, walk-forward)
  evaluation.ew_universe       all | risky                    (what the EW benchmark holds)
  gates.type                   relative_return | risk_reduction
Never touches sessions after periods.dev_end. The holdout lives in holdout.py.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from strategylab.config import load_config, results_dir
from strategylab.data.panel import build_panel
from strategylab.data.validate import validate
from strategylab.engine.backtest import Result, buy_hold, equal_weight, model_weights, run
from strategylab.engine.costs import CostModel
from strategylab.engine.schedule import decision_schedule, eligibility
from strategylab.evaluation import stats as st
from strategylab.models import load_model

MIN_SCORED_ASSETS = 8
HORIZONS = (1, 3, 6, 12)


# ----- helpers ------------------------------------------------------------------------------

def signal_specs(model, specs) -> dict:
    """One representative spec per distinct signal (signal tests do not depend on portfolio parameters)."""
    reps = {}
    for s in specs:
        reps.setdefault(model.signal_key(s), s)
    return reps


def common_start(model, levels, returns, cfg, specs, sched) -> pd.Timestamp:
    """First decision date on which every trial's signal scores >= MIN_SCORED_ASSETS eligible assets."""
    elig = eligibility(levels, returns, cfg["universe"]["min_history_sessions"])
    ok = pd.Series(True, index=sched["decision"])
    for spec in signal_specs(model, specs).values():
        sc = model.signal(levels, returns, spec, cfg)
        n = (sc.notna() & elig).sum(axis=1).reindex(sched["decision"])
        ok &= (n >= MIN_SCORED_ASSETS).to_numpy()
    return ok[ok].index[0]


def benchmarks(cfg) -> tuple[str, str]:
    """(display name, asset id) of the buy-and-hold market benchmark."""
    return next((k, v) for k, v in cfg.get("benchmarks", {}).items() if isinstance(v, str))


def make_cost_model(cfg, panel, multiplier=1.0) -> CostModel:
    return CostModel(cfg["costs"], {a: v * 1e7 for a, v in panel.etf_adv_cr.items()},
                     {a["id"]: a["tax_class"] for a in cfg["universe"]["assets"]}, multiplier)


def annual_cost_drag(res: Result) -> float:
    c = res.costs.reindex(res.nav.index).fillna(0) / res.nav.shift(1).bfill()
    years = (res.nav.index[-1] - res.nav.index[0]).days / 365.25
    return float(c.sum() / years)


def capacity_inr(res: Result, panel, cfg) -> float:
    """Capital at which the 95th-percentile trade reaches max_participation of its ETF's ADV.

    Trades are scaled by the NAV on their date, so the answer does not depend on
    how much the backtest's capital happened to grow.
    """
    t = res.trades[res.trades["side"] != "sell_tax"].copy()
    nav_then = res.nav.reindex(pd.to_datetime(t["date"])).to_numpy()
    t["part_per_inr"] = t["value"] / nav_then / t["asset"].map(lambda a: panel.etf_adv_cr[a] * 1e7)
    return float(cfg["costs"]["max_participation"] / t["part_per_inr"].quantile(0.95))


def ann_sr(x: pd.Series) -> float:
    return float(x.mean() / x.std() * np.sqrt(12))


def pct(x, d=1):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}%}"


def num(x, d=2):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}"


# ----- gate rules -----------------------------------------------------------------------------

def strategy_rule(gtype: str, g: dict, p: dict, e: dict) -> tuple[bool, str]:
    """Strategy gate on net-of-cost, pre-tax performance p vs EW e."""
    if gtype == "relative_return":
        ok = bool(p["CAGR"] > e["CAGR"] and p["Sharpe"] > 0.5 and p["MaxDD"] >= e["MaxDD"])
        return ok, (f"CAGR {pct(p['CAGR'])} vs EW {pct(e['CAGR'])}; Sharpe {num(p['Sharpe'])}; "
                    f"MaxDD {pct(p['MaxDD'])} vs EW {pct(e['MaxDD'])}")
    limit = g["max_dd_ratio_vs_ew"] * e["MaxDD"]
    ok = bool(p["MaxDD"] >= limit and p["Sharpe"] > e["Sharpe"] and p["Calmar"] > e["Calmar"])
    return ok, (f"MaxDD {pct(p['MaxDD'])} vs limit {pct(limit)} (EW {pct(e['MaxDD'])}); "
                f"Sharpe {num(p['Sharpe'])} vs EW {num(e['Sharpe'])}; Calmar {num(p['Calmar'])} vs EW {num(e['Calmar'])}")


def drawdown_ok(g: dict, p: dict, e: dict) -> bool:
    return bool(p["MaxDD"] >= g["max_dd_ratio_vs_ew"] * e["MaxDD"])


def _json_default(o):
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (np.floating, np.integer)):
        return float(o)
    return str(o)


# ----- main ----------------------------------------------------------------------------------

def main(strategy_id: str) -> None:
    cfg = load_config(strategy_id)
    model = load_model(cfg["model"])
    ev = cfg["evaluation"]
    g = cfg.get("gates", {"type": "relative_return"})
    gtype = g["type"]
    sel_kind = ev.get("selection_series", "active_vs_ew")
    sel_label = "active vs EW" if sel_kind == "active_vs_ew" else "excess over cash"
    signal_test = ev.get("signal_test", getattr(model, "SIGNAL_TEST", "cross_sectional"))
    OUT = results_dir(strategy_id)
    FIG = OUT / "figures"
    FIG.mkdir(parents=True, exist_ok=True)
    panel = build_panel(cfg)

    # ---- Gate 1: data -----------------------------------------------------------------
    data_ok, _ = validate(panel, cfg, OUT)
    print(f"[data gate] {'PASS' if data_ok else 'FAIL'}  -> {OUT / 'data_validation.md'}")

    dev_end = pd.Timestamp(cfg["periods"]["dev_end"])
    lv = panel.levels.loc[:dev_end]
    rt = panel.returns.loc[:dev_end]
    rf = panel.rf.loc[:dev_end]
    cash_id = cfg["universe"].get("cash_asset")
    specs = model.grid(cfg)
    prim = model.primary(cfg)
    assert prim in specs, "primary spec must be one of the grid cells"
    fields = list(asdict(prim))
    sched = decision_schedule(lv.index, cfg["decision"]["execution_lag_sessions"], cfg["decision"]["cadence"])
    start = common_start(model, lv, rt, cfg, specs, sched)
    sched = sched[sched["decision"] >= start]
    print(f"[{strategy_id}] {start.date()} .. {dev_end.date()}  ({len(sched)} decisions)")

    elig = eligibility(lv, rt, cfg["universe"]["min_history_sessions"])
    if ev.get("ew_universe", "all") == "risky":
        risky = [a["id"] for a in cfg["universe"]["assets"] if a.get("role", "risky") == "risky"]
        ew_elig = elig & pd.Series(elig.columns.isin(risky), index=elig.columns)
    else:
        ew_elig = elig

    # ---- Gate 2: signal ----------------------------------------------------------------------
    ex = sched["execution"].to_numpy()
    lag = ev["newey_west_lags"]

    def fwd_returns(h: int) -> pd.DataFrame:
        """Return from the execution close of decision i to the execution close of decision i+h."""
        lvx = lv.loc[ex]
        f = lvx.shift(-h) / lvx - 1
        f.index = sched["decision"].to_numpy()
        return f

    fwd = {h: fwd_returns(h) for h in HORIZONS}
    dates = fwd[1].index[:-1]
    sig_rows, sig_prim = [], None
    for key, spec in sorted(signal_specs(model, specs).items()):
        sc = model.signal(lv, rt, spec, cfg)
        if signal_test == "cross_sectional":
            s1 = st.ic_series(sc, fwd[1], elig, dates)
            decay = {f"IC_{h}m": st.ic_series(sc, fwd[h], elig, fwd[1].index[:-h]).mean() for h in HORIZONS}
            row = {"signal": key, "mean_IC": s1.mean(), "IC_IR": s1.mean() / s1.std()}
            extra = {"hit_rate": (s1 > 0).mean()}
        else:
            excess = {h: fwd[h].sub(fwd[h][cash_id], axis=0) for h in HORIZONS}
            sp = st.ts_spread_series(sc, excess[1], elig, dates)
            s1 = sp["spread"].dropna()
            decay = {f"spread_{h}m": st.ts_spread_series(sc, excess[h], elig, fwd[1].index[:-h])["spread"].mean()
                     for h in HORIZONS}
            row = {"signal": key, "mean_spread_m": s1.mean(), "on_excess_m": sp["on_mean"].mean(),
                   "off_excess_m": sp["off_mean"].mean()}
            extra = {"share_on": sp["share_on"].mean(), "IC_xs": st.ic_series(sc, fwd[1], elig, dates).mean()}
        half = len(s1) // 2
        row.update({"t_NW": st.newey_west_t(s1, lag), "1st_half": s1.iloc[:half].mean(),
                    "2nd_half": s1.iloc[half:].mean(), **extra, "n": len(s1), **decay})
        sig_rows.append(row)
        if key == model.signal_key(prim):
            sig_prim = s1
    sig_table = pd.DataFrame(sig_rows)
    prow = sig_table[sig_table["signal"] == model.signal_key(prim)].iloc[0]
    head = "mean_IC" if signal_test == "cross_sectional" else "mean_spread_m"
    signal_ok = bool(prow["t_NW"] > 2.0 and prow["1st_half"] * prow["2nd_half"] > 0 and prow[head] > 0)
    suspicious = bool(signal_test == "cross_sectional" and prow["mean_IC"] > 0.15)
    print(f"[signal gate] {'PASS' if signal_ok else 'FAIL'}  {head}={prow[head]:.4f} t_NW={prow['t_NW']:.2f}")

    # ---- Backtests --------------------------------------------------------------------
    cm1 = make_cost_model(cfg, panel, 1.0)
    kw = dict(start=start, end=dev_end)
    bh_name, bh_asset = benchmarks(cfg)
    ref_name, ref_series = next(iter(panel.reference.items()), (None, None))
    strat = lambda spec: model_weights(model, spec, lv, rt, elig, cfg)  # noqa: E731
    print("[backtest] benchmarks")
    ew_net = run("EW", lv, rt, cfg, cm1, weights_fn=equal_weight(ew_elig), tax=False, **kw)
    ew_tax = run("EW", lv, rt, cfg, cm1, weights_fn=equal_weight(ew_elig), tax=True, **kw)
    mkt_tax = run(bh_name, lv, rt, cfg, cm1, weights_fn=buy_hold(bh_asset, lv.columns), tax=True, **kw)
    ew_m = st.monthly(ew_net.nav)
    cash_m = lv[cash_id].resample("ME").last().pct_change() if cash_id else None

    def sel_series(nav: pd.Series) -> pd.Series:
        m = st.monthly(nav)
        return (m - ew_m) if sel_kind == "active_vs_ew" else (m - cash_m.reindex(m.index))

    nets, taxed = {}, {}
    for i, spec in enumerate(specs):
        print(f"[backtest] {i + 1:2d}/{len(specs)} {spec.name}")
        nets[spec] = run(spec.name, lv, rt, cfg, cm1, weights_fn=strat(spec), tax=False, **kw)
        taxed[spec] = run(spec.name, lv, rt, cfg, cm1, weights_fn=strat(spec), tax=True, **kw)

    pe = st.perf(ew_net.nav, rf)
    pet = st.perf(ew_tax.nav, rf, ew_tax.after_tax_terminal)
    sel_m = pd.DataFrame({s.name: sel_series(nets[s].nav) for s in specs}).dropna()
    active_m = pd.DataFrame({s.name: st.monthly(nets[s].nav) - ew_m for s in specs}).dropna()
    rows = []
    for s in specs:
        p = st.perf(nets[s].nav, rf)
        pt = st.perf(taxed[s].nav, rf, taxed[s].after_tax_terminal)
        turnover = nets[s].trades.query("side != 'sell_tax'")["value"].sum() / 2 / nets[s].nav.mean()
        years = (nets[s].nav.index[-1] - nets[s].nav.index[0]).days / 365.25
        rows.append({"trial": s.name, **asdict(s), "primary": s == prim,
                     "CAGR_net": p["CAGR"], "Sharpe_net": p["Sharpe"], "MaxDD": p["MaxDD"], "Calmar": p["Calmar"],
                     "CAGR_after_tax": pt["CAGR_after_tax"], "MaxDD_after_tax": pt["MaxDD"],
                     "active_ann": active_m[s.name].mean() * 12, "active_IR": ann_sr(active_m[s.name]),
                     "sel_SR": ann_sr(sel_m[s.name]), "turnover_pa": turnover / years,
                     "passes_strategy_rule": strategy_rule(gtype, g, p, pe)[0]})
    trials = pd.DataFrame(rows)

    # ---- Selection statistics (on the selection series) ---------------------------------------
    trial_sr = (sel_m.mean() / sel_m.std()).to_numpy()
    dsr = st.deflated_sharpe(sel_m[prim.name], trial_sr)
    dsr_best = st.deflated_sharpe(sel_m[trials.loc[trials["sel_SR"].idxmax(), "trial"]], trial_sr)
    pbo = st.pbo_cscv(sel_m, ev["pbo_blocks"])
    haircut = st.haircut_sharpe(trials["sel_SR"].max(), trials["sel_SR"].to_numpy())
    boot_p = st.block_bootstrap_p(sel_m[prim.name], ev["bootstrap_draws"])
    sr_diff_p = st.sharpe_diff_bootstrap_p(
        st.monthly(nets[prim].nav) - cash_m, ew_m - cash_m, ev["bootstrap_draws"]) if cash_id else np.nan
    robustness = float(trials["passes_strategy_rule"].mean()) if gtype == "risk_reduction" \
        else float((trials["active_IR"] > 0).mean())
    if gtype == "relative_return":
        selection_ok = bool(dsr["dsr"] > 0.95 and pbo["pbo"] < 0.5)
        sel_ev = f"DSR {dsr['dsr']:.3f} (need > 0.95); PBO {pbo['pbo']:.2f} (need < 0.50)"
    else:
        selection_ok = bool(dsr["dsr"] > 0.95 and pbo["pbo"] < 0.5 and sr_diff_p < g["bootstrap_p_max"])
        sel_ev = (f"DSR {dsr['dsr']:.3f} (need > 0.95); PBO {pbo['pbo']:.2f} (need < 0.50); "
                  f"Sharpe-vs-EW bootstrap p {sr_diff_p:.3f} (need < {g['bootstrap_p_max']})")
    print(f"[selection gate] {'PASS' if selection_ok else 'FAIL'}  {sel_ev}")

    # ---- Walk-forward selection ---------------------------------------------------------------
    wf_cfg = ev["walk_forward_selection"]
    wf = {}
    for kind in ("expanding", "rolling"):
        picks, out = [], []
        for y in sorted(set(sel_m.index.year)):
            hist = sel_m[sel_m.index.year < y]
            if kind == "rolling":
                hist = hist[hist.index.year >= y - wf_cfg["min_train_years"]]
            if hist.index.year.nunique() < wf_cfg["min_train_years"]:
                continue
            best = (hist.mean() / hist.std()).idxmax()
            picks.append((y, best))
            out.append(sel_m.loc[sel_m.index.year == y, best])
        s_ = pd.concat(out)
        wf[kind] = {"series": s_, "picks": picks, "ann": s_.mean() * 12, "SR": ann_sr(s_)}
    prim_same_window = sel_m.loc[wf["expanding"]["series"].index[0]:, prim.name]

    # ---- Costs --------------------------------------------------------------------------------
    cost_rows = []
    for m in ev["cost_multipliers"]:
        cm = make_cost_model(cfg, panel, m)
        r_s = nets[prim] if m == 1 else run(prim.name, lv, rt, cfg, cm, weights_fn=strat(prim), tax=False, **kw)
        r_b = ew_net if m == 1 else run("EW", lv, rt, cfg, cm, weights_fn=equal_weight(ew_elig), tax=False, **kw)
        ps, pb = st.perf(r_s.nav, rf), st.perf(r_b.nav, rf)
        cost_rows.append({"cost_x": m, "CAGR": ps["CAGR"], "Sharpe": ps["Sharpe"], "MaxDD": ps["MaxDD"],
                          "EW_CAGR": pb["CAGR"], "EW_Sharpe": pb["Sharpe"],
                          "active_ann": (st.monthly(r_s.nav) - st.monthly(r_b.nav)).mean() * 12,
                          "sel_ann": sel_series(r_s.nav).mean() * 12, "cost_drag_pa": annual_cost_drag(r_s)})
    costs = pd.DataFrame(cost_rows)
    c0, c1, c3 = (costs.set_index("cost_x").loc[k] for k in (0, 1, 3))
    gross_edge = c0["active_ann"] if gtype == "relative_return" else c0["sel_ann"]
    safety_total = gross_edge / c1["cost_drag_pa"] if c1["cost_drag_pa"] > 0 and gross_edge > 0 else 0.0
    if gtype == "relative_return":
        cost_ok = bool(safety_total >= 2.5 and c3["active_ann"] > 0)
        cost_ev = f"safety margin {safety_total:.1f}x (need >= 2.5x); active at 3x costs {pct(c3['active_ann'], 2)}"
    else:
        cost_ok = bool(c3["Sharpe"] > c3["EW_Sharpe"])
        cost_ev = (f"at 3x costs Sharpe {num(c3['Sharpe'])} vs EW {num(c3['EW_Sharpe'])}; "
                   f"cost drag {pct(c1['cost_drag_pa'], 2)} a year")
    cap = capacity_inr(nets[prim], panel, cfg)
    cost_split = nets[prim].trades.query("side != 'sell_tax'")[["explicit", "spread", "impact"]].sum()

    # ---- Strategy and tax gates -----------------------------------------------------------------
    pp = st.perf(nets[prim].nav, rf)
    ppt = st.perf(taxed[prim].nav, rf, taxed[prim].after_tax_terminal)
    pmt = st.perf(mkt_tax.nav, rf, mkt_tax.after_tax_terminal)
    strategy_ok, strat_ev = strategy_rule(gtype, g, pp, pe)
    if gtype == "relative_return":
        tax_ok = bool(ppt["CAGR_after_tax"] > pet["CAGR_after_tax"])
        tax_ev = f"after-tax CAGR {pct(ppt['CAGR_after_tax'])} vs EW {pct(pet['CAGR_after_tax'])}"
    else:
        tol = g["tax_cagr_tolerance_pp"] / 100
        tax_ok = bool(ppt["CAGR_after_tax"] >= pet["CAGR_after_tax"] - tol and drawdown_ok(g, ppt, pet))
        tax_ev = (f"after-tax CAGR {pct(ppt['CAGR_after_tax'])} vs EW {pct(pet['CAGR_after_tax'])} "
                  f"(tolerance {g['tax_cagr_tolerance_pp']} pp); after-tax MaxDD {pct(ppt['MaxDD'])} "
                  f"vs limit {pct(g['max_dd_ratio_vs_ew'] * pet['MaxDD'])}")
    ref_cagr = np.nan
    if ref_series is not None:
        r_ = ref_series.loc[nets[prim].nav.index[0]:dev_end].dropna()
        ref_cagr = (r_.iloc[-1] / r_.iloc[0]) ** (365.25 / (r_.index[-1] - r_.index[0]).days) - 1

    head_label = "IC" if signal_test == "cross_sectional" else "on-off spread (monthly)"
    gates = [
        ("Data", data_ok, "see data_validation.md"),
        ("Signal", signal_ok, f"{head_label} {prow[head]:.4f}, NW t {prow['t_NW']:.2f}, halves "
                              f"{prow['1st_half']:.4f} / {prow['2nd_half']:.4f}"),
        ("Strategy", strategy_ok, strat_ev),
        ("Selection", selection_ok, sel_ev),
        ("Costs", cost_ok, cost_ev),
        ("Tax", tax_ok, tax_ev),
    ]
    both_selection_fail = dsr["dsr"] <= 0.95 and pbo["pbo"] >= 0.5
    if gtype == "relative_return":
        killed = bool((not signal_ok) or both_selection_fail)
    else:
        killed = bool((not signal_ok) or (not drawdown_ok(g, pp, pe)) or both_selection_fail)

    # ---- Figures -----------------------------------------------------------------------------
    navs = pd.DataFrame({"Strategy (net)": nets[prim].nav, "EW (net)": ew_net.nav,
                         f"{bh_name} (buy & hold)": mkt_tax.nav})
    navs = navs / navs.iloc[0]
    fig, ax = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]})
    navs.plot(ax=ax[0], logy=True, lw=1.3)
    ax[0].set_title(f"Development period, pre-tax, net of costs — primary {prim.name}")
    (navs / navs.cummax() - 1).plot(ax=ax[1], lw=1, legend=False)
    ax[1].set_ylabel("drawdown")
    fig.tight_layout(); fig.savefig(FIG / "dev_equity_drawdown.png", dpi=130); plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 3.5))
    sig_prim.plot(ax=ax, lw=0.6, alpha=0.6, label=f"{head_label} per decision")
    sig_prim.rolling(24, min_periods=6).mean().plot(ax=ax, lw=1.8, label="24-decision mean")
    ax.axhline(0, color="k", lw=0.6); ax.legend(); ax.set_title(f"{head_label}, {model.signal_key(prim)}")
    fig.tight_layout(); fig.savefig(FIG / "dev_signal.png", dpi=130); plt.close(fig)

    facet, col = fields[0], fields[-1]
    mids = fields[1:-1] or [fields[0]]
    tr = trials.assign(_row=trials[mids].astype(str).agg("/".join, axis=1))
    metric = "active_IR" if gtype == "relative_return" else "sel_SR"
    groups = list(tr.groupby(facet))
    fig, axes = plt.subplots(1, len(groups), figsize=(5.5 * len(groups), 3.6), squeeze=False)
    for ax_, (fv, sub) in zip(axes[0], groups):
        hm = sub.pivot(index="_row", columns=col, values=metric)
        lim = max(1.0, float(np.nanmax(np.abs(trials[metric]))))
        im = ax_.imshow(hm.values, cmap="RdBu", vmin=-lim, vmax=lim)
        ax_.set_xticks(range(len(hm.columns)), hm.columns); ax_.set_yticks(range(len(hm.index)), hm.index)
        for (i_, j_), v in np.ndenumerate(hm.values):
            ax_.text(j_, i_, f"{v:.2f}", ha="center", va="center", fontsize=9)
        ax_.set_title(f"{metric} — {facet}={fv}"); ax_.set_xlabel(col); ax_.set_ylabel("/".join(mids))
    fig.colorbar(im, ax=axes, shrink=0.8)
    fig.savefig(FIG / "dev_sensitivity.png", dpi=130); plt.close(fig)

    rs = sel_m[prim.name]
    fig, ax = plt.subplots(figsize=(10, 3.2))
    (rs.rolling(36).mean() / rs.rolling(36).std() * np.sqrt(12)).plot(ax=ax)
    ax.axhline(0, color="k", lw=0.6); ax.set_title(f"Rolling 36-month Sharpe of the primary, {sel_label}")
    fig.tight_layout(); fig.savefig(FIG / "dev_rolling_selection_sr.png", dpi=130); plt.close(fig)

    # ---- Report --------------------------------------------------------------------------
    perf_tbl = pd.DataFrame({"Primary (net)": pp, "EW (net)": pe, "Primary (after tax)": ppt,
                             "EW (after tax)": pet, f"{bh_name} B&H (after tax)": pmt}).T
    fmt_pct = ["CAGR", "Vol", "MaxDD", "VaR95_d", "CVaR95_d", "Win_m", "CAGR_after_tax"]
    perf_fmt = perf_tbl.copy().astype(object)
    for c in perf_tbl.columns:
        perf_fmt[c] = [pct(v) if c in fmt_pct else (f"{v:.0f}" if c == "MaxDD_days" else num(v)) for v in perf_tbl[c]]
    tr_fmt = trials.drop(columns=fields).copy()
    for c in ["CAGR_net", "MaxDD", "CAGR_after_tax", "MaxDD_after_tax", "active_ann"]:
        tr_fmt[c] = tr_fmt[c].map(pct)
    for c in ["Sharpe_net", "Calmar", "active_IR", "sel_SR", "turnover_pa"]:
        tr_fmt[c] = tr_fmt[c].map(num)
    sig_fmt = sig_table.copy()
    for c in sig_fmt.columns[1:]:
        sig_fmt[c] = sig_fmt[c].map(lambda v: f"{v:.4f}" if isinstance(v, float) else v)
    cost_fmt = costs.copy()
    for c in ["CAGR", "MaxDD", "EW_CAGR", "active_ann", "sel_ann", "cost_drag_pa"]:
        cost_fmt[c] = cost_fmt[c].map(lambda v: pct(v, 2))
    for c in ["Sharpe", "EW_Sharpe"]:
        cost_fmt[c] = cost_fmt[c].map(num)
    if signal_test == "cross_sectional":
        sig_text = ("Monthly cross-sectional rank IC: Spearman correlation between the score at the decision close "
                    "and the return from the next execution close to the following one. `IC_hm` is the mean IC "
                    "against the h-month forward return (decay).")
    else:
        sig_text = ("Time-series test: at each decision, the mean forward excess-over-cash return of assets whose "
                    "signal is on minus those whose signal is off (execution close to next execution close). "
                    "`spread_hm` uses h-month forward returns; `IC_xs` is the cross-sectional rank IC, for reference.")
    last_w = nets[prim].weights.iloc[-1]
    wf_label = len(specs) + 1
    lines = [
        "# Development-period report", "",
        f"Strategy: **{strategy_id}** (model `{cfg['model']}`, gates `{gtype}`). Primary spec: **{prim.name}**. "
        f"Period: {start.date()} to {dev_end.date()} ({len(sel_m)} months). "
        f"Holdout (from {cfg['periods']['holdout_start']}) not touched.", "",
        "## Gates (pre-registered in HYPOTHESIS.md)", "",
        "| Gate | Result | Evidence |", "|---|---|---|",
        *[f"| {n} | {'PASS' if ok else 'FAIL'} | {e} |" for n, ok, e in gates], "",
        f"**Kill criterion triggered: {'YES — strategy rejected' if killed else 'no'}**", "",
    ]
    if suspicious:
        lines += ["> Mean IC above 0.15: treat as a probable bug or lookahead, not as success.", ""]
    lines += [
        "## Performance (development period)", "",
        "Sharpe and Sortino are on returns in excess of the cash asset (6% p.a. before it has history). "
        "After-tax CAGR includes tax paid each April and tax on liquidating the book on the last day.", "",
        perf_fmt.to_markdown(), "",
        (f"Reference {ref_name} (price series, no costs/tax): CAGR {pct(ref_cagr)} over the same window."
         if ref_name else ""), "",
        "![equity](figures/dev_equity_drawdown.png)", "",
        "## Signal research (development period)", "",
        sig_text + f" `t_NW` uses Newey-West errors ({lag} lags).", "",
        sig_fmt.to_markdown(index=False), "",
        "![signal](figures/dev_signal.png)", "",
        f"## All {len(specs)} trials (net of costs; active = vs EW; sel_SR = annualised Sharpe of the {sel_label} series)", "",
        tr_fmt.to_markdown(index=False), "",
        "![sensitivity](figures/dev_sensitivity.png)", "",
        f"Robustness: {robustness:.0%} of the grid "
        + ("has positive active IR" if gtype == "relative_return" else "passes the strategy-gate rule")
        + " (sensitivity-analysis skill targets > 60%).", "",
        f"## Selection statistics ({sel_label}, monthly)", "",
        f"- Deflated Sharpe Ratio of the primary, N = {len(specs)}: **{dsr['dsr']:.3f}** (per-month SR {dsr['sr']:.3f} "
        f"vs expected max under null {dsr['e_max_sr']:.3f}; skew {dsr['skew']:.2f}, kurtosis {dsr['kurtosis']:.2f}; "
        f"n = {dsr['n']}).",
        f"- PSR (ignoring selection): {dsr['psr_vs_0']:.3f}; block-bootstrap one-sided p-value for mean > 0: {boot_p:.3f}.",
        f"- DSR of the best grid cell: {dsr_best['dsr']:.3f}.",
        f"- Sharpe haircut on the best annualised Sharpe ({trials['sel_SR'].max():.2f}): {haircut:+.2f}.",
        f"- PBO (CSCV, {ev['pbo_blocks']} blocks, {pbo['n_splits']} splits): **{pbo['pbo']:.2f}**; in-sample-best "
        f"per-month SR {pbo['is_sr_best_mean']:.3f} -> out-of-sample {pbo['oos_sr_best_mean']:.3f}.",
        f"- Paired block bootstrap, Sharpe(primary) > Sharpe(EW) on excess-over-cash returns: one-sided p = {num(sr_diff_p, 3)}.",
        "",
        f"## Walk-forward selection (trial {wf_label})", "",
        f"Each January, pick the grid cell with the best trailing Sharpe of its {sel_label} series and hold it for the "
        "year (stitched monthly returns; switching costs between cells are ignored).", "",
        "| Variant | Mean p.a. | Sharpe | Primary, same window |", "|---|---|---|---|",
        *[f"| {k} | {pct(v['ann'], 2)} | {num(v['SR'])} | {pct(prim_same_window.mean() * 12, 2)} / "
          f"{num(ann_sr(prim_same_window))} |" for k, v in wf.items()], "",
        "Picks (expanding): " + ", ".join(f"{y}: {s}" for y, s in wf["expanding"]["picks"]), "",
        "![rolling](figures/dev_rolling_selection_sr.png)", "",
        "## Costs, capacity", "",
        cost_fmt.to_markdown(index=False), "",
        f"- Safety margin (gross {'active' if gtype == 'relative_return' else 'excess-over-cash'} return / cost drag): "
        f"**{safety_total:.1f}x**.",
        f"- Cost split at 1x (rupees over the period): explicit {cost_split['explicit']:,.0f}, "
        f"spread {cost_split['spread']:,.0f}, impact {cost_split['impact']:,.0f}.",
        f"- Capacity (95th-percentile trade at <= {cfg['costs']['max_participation']:.0%} of current ETF ADV): "
        f"about **Rs {cap / 1e7:,.1f} crore**.", "",
        "## Latest development-period holdings", "",
        ", ".join(f"{a} {w:.0%}" for a, w in last_w[last_w > 0].items()), "",
    ]
    (OUT / "REPORT_dev.md").write_text("\n".join(lines) + "\n")

    trial_lines = ["# Trials log", "",
                   f"Pre-registered grid: {len(specs)} trials + walk-forward selection = {len(specs) + 1}.",
                   "Any rerun with a changed specification must be appended here.", "",
                   "| # | Trial | Source |", "|---|---|---|"]
    trial_lines += [f"| {i + 1} | {s.name} | grid{' (primary)' if s == prim else ''} |" for i, s in enumerate(specs)]
    trial_lines += [f"| {wf_label} | walk-forward selection | grid |"]
    (OUT / "TRIALS.md").write_text("\n".join(trial_lines) + "\n")

    # ---- Artifacts for the dashboard -----------------------------------------------------
    art = OUT / "artifacts" / "dev"
    art.mkdir(parents=True, exist_ok=True)
    idx = nets[prim].nav.index
    pd.DataFrame({"primary_net": nets[prim].nav, "primary_after_tax": taxed[prim].nav,
                  "ew_net": ew_net.nav, "ew_after_tax": ew_tax.nav, "market_after_tax": mkt_tax.nav,
                  "reference_price": ref_series.reindex(idx) if ref_series is not None else np.nan,
                  "rf": rf.reindex(idx)}).to_parquet(art / "nav.parquet")
    pd.DataFrame({s.name: nets[s].nav for s in specs}).to_parquet(art / "trial_nav.parquet")
    nets[prim].weights.to_parquet(art / "weights.parquet")
    nets[prim].trades.to_parquet(art / "trades.parquet")
    taxed[prim].trades.to_parquet(art / "trades_after_tax.parquet")
    pd.DataFrame({"costs": nets[prim].costs}).to_parquet(art / "costs.parquet")
    taxed[prim].tax_paid.rename("tax").to_frame().to_parquet(art / "tax_paid.parquet")
    ew_tax.tax_paid.rename("tax").to_frame().to_parquet(art / "ew_tax_paid.parquet")
    trials.to_parquet(art / "trials.parquet")
    sig_table.to_parquet(art / "signal_table.parquet")
    sig_prim.rename("value").to_frame().to_parquet(art / "signal_series.parquet")
    active_m.to_parquet(art / "active_monthly.parquet")
    sel_m.to_parquet(art / "selection_monthly.parquet")
    costs.to_parquet(art / "cost_sensitivity.parquet")
    pd.DataFrame({k: v["series"] for k, v in wf.items()}).to_parquet(art / "walk_forward.parquet")
    (art / "stats.json").write_text(json.dumps({
        "strategy_id": strategy_id, "model": cfg["model"], "gate_type": gtype,
        "signal_test": signal_test, "signal_label": head_label, "selection_label": sel_label,
        "spec_fields": fields, "benchmark_names": {"market": bh_name, "reference": ref_name},
        "primary": prim.name, "start": str(start.date()), "end": str(dev_end.date()),
        "months": len(sel_m), "n_trials": len(specs),
        "gates": [{"gate": n, "pass": ok, "evidence": e} for n, ok, e in gates], "killed": killed,
        "perf": {k: {m: (None if pd.isna(v) else float(v)) for m, v in d.items()}
                 for k, d in {"primary_net": pp, "ew_net": pe, "primary_after_tax": ppt,
                              "ew_after_tax": pet, "market_after_tax": pmt}.items()},
        "reference_cagr": ref_cagr,
        "dsr": dsr, "dsr_best": dsr_best, "pbo": pbo, "haircut": haircut, "bootstrap_p": boot_p,
        "sharpe_diff_p": sr_diff_p, "robustness": robustness,
        "walk_forward": {k: {"ann": v["ann"], "SR": v["SR"], "picks": v["picks"]} for k, v in wf.items()},
        "primary_same_window": {"ann": prim_same_window.mean() * 12, "SR": ann_sr(prim_same_window)},
        "safety_margin": safety_total, "capacity_inr": cap, "cost_split": cost_split.to_dict(),
        "liquidation_tax": taxed[prim].liquidation_tax, "ew_liquidation_tax": ew_tax.liquidation_tax,
        "suspicious_signal": suspicious,
    }, indent=2, default=_json_default))

    summary = {"start": str(start.date()), "dev_end": str(dev_end.date()),
               "gates": {n: ok for n, ok, _ in gates}, "killed": killed, "dsr": dsr["dsr"], "pbo": pbo["pbo"],
               "signal": float(prow[head]), "signal_t": float(prow["t_NW"]), "capacity_inr": cap}
    (OUT / "dev_summary.json").write_text(json.dumps(summary, indent=2, default=_json_default))
    print("\n".join(f"  {n:10s} {'PASS' if ok else 'FAIL'}  {e}" for n, ok, e in gates))
    print(f"  killed: {killed}\n-> {OUT / 'REPORT_dev.md'}")


if __name__ == "__main__":
    import sys
    main(sys.argv[1])
