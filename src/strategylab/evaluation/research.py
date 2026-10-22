"""Development-period research: data gate, signal gate, 24-trial grid, selection
statistics, cost sensitivity, capacity, and the pre-registered gate table.

Works for any strategy whose config names a registered model (see models/).
Never touches sessions after periods.dev_end. The holdout lives in holdout.py.
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from strategylab.evaluation import stats as st
from strategylab.engine.backtest import Result, buy_hold, equal_weight, model_weights, run
from strategylab.config import load_config, results_dir
from strategylab.engine.costs import CostModel
from strategylab.models import load_model
from strategylab.data.panel import build_panel
from strategylab.engine.schedule import decision_schedule, eligibility
from strategylab.data.validate import validate

MIN_SCORED_ASSETS = 8


def signal_specs(model, specs) -> dict:
    """One representative spec per distinct signal (IC does not depend on portfolio parameters)."""
    reps = {}
    for s in specs:
        reps.setdefault(model.signal_key(s), s)
    return reps


def common_start(model, levels, returns, cfg, specs, sched) -> pd.Timestamp:
    """First decision date on which every trial's signal scores >= MIN_SCORED_ASSETS eligible assets."""
    elig = eligibility(levels, returns, cfg["universe"]["min_history_sessions"])
    ok = pd.Series(True, index=sched["decision"])
    for spec in signal_specs(model, specs).values():
        sc = model.signal(levels, returns, spec)
        n = (sc.notna() & elig).sum(axis=1).reindex(sched["decision"])
        ok &= (n >= MIN_SCORED_ASSETS).to_numpy()
    return ok[ok].index[0]


def benchmarks(cfg) -> tuple[str, str]:
    """(display name, asset id) of the buy-and-hold market benchmark."""
    name, asset = next((k, v) for k, v in cfg.get("benchmarks", {}).items() if isinstance(v, str))
    return name, asset


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


def pct(x, d=1):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}%}"


def num(x, d=2):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}"


def main(strategy_id: str) -> None:
    cfg = load_config(strategy_id)
    model = load_model(cfg["model"])
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
    specs = model.grid(cfg)
    prim = model.primary(cfg)
    assert prim in specs, "primary spec must be one of the grid cells"
    sched = decision_schedule(lv.index, cfg["decision"]["execution_lag_sessions"], cfg["decision"]["cadence"])
    start = common_start(model, lv, rt, cfg, specs, sched)
    sched = sched[sched["decision"] >= start]
    print(f"[{strategy_id}] {start.date()} .. {dev_end.date()}  ({len(sched)} decisions)")

    # ---- Gate 2: signal (cross-sectional rank IC) ---------------------------------------
    elig = eligibility(lv, rt, cfg["universe"]["min_history_sessions"])
    ex = sched["execution"].to_numpy()
    lag = cfg["evaluation"]["newey_west_lags"]

    def fwd_returns(h: int) -> pd.DataFrame:
        """Return from execution close of decision i to execution close of decision i+h."""
        lvx = lv.loc[ex]
        f = lvx.shift(-h) / lvx - 1
        f.index = sched["decision"].to_numpy()
        return f

    fwd1 = fwd_returns(1)
    ic_rows, ic_prim = [], None
    for key, spec in sorted(signal_specs(model, specs).items()):
        sc = model.signal(lv, rt, spec)
        ic = st.ic_series(sc, fwd1, elig, fwd1.index[:-1])
        half = len(ic) // 2
        decay = {}
        for h in (1, 3, 6, 12):
            ich = st.ic_series(sc, fwd_returns(h), elig, fwd1.index[:-h])
            decay[f"IC_{h}m"] = ich.mean()
        row = {"signal": key,
               "mean_IC": ic.mean(), "IC_IR": ic.mean() / ic.std(), "t_NW": st.newey_west_t(ic, lag),
               "IC_1st_half": ic.iloc[:half].mean(), "IC_2nd_half": ic.iloc[half:].mean(),
               "hit_rate": (ic > 0).mean(), "n": len(ic), **decay}
        ic_rows.append(row)
        if key == model.signal_key(prim):
            ic_prim = ic
    ic_table = pd.DataFrame(ic_rows)
    prow = ic_table[ic_table["signal"] == model.signal_key(prim)].iloc[0]
    signal_ok = bool(prow["t_NW"] > 2.0 and prow["IC_1st_half"] * prow["IC_2nd_half"] > 0 and prow["mean_IC"] > 0)
    suspicious_ic = bool(prow["mean_IC"] > 0.15)
    print(f"[signal gate] {'PASS' if signal_ok else 'FAIL'}  IC={prow['mean_IC']:.3f} t_NW={prow['t_NW']:.2f}")

    # ---- Backtests --------------------------------------------------------------------
    cm1 = make_cost_model(cfg, panel, 1.0)
    end = dev_end
    kw = dict(start=start, end=end)
    bh_name, bh_asset = benchmarks(cfg)
    ref_name, ref_series = next(iter(panel.reference.items()), (None, None))
    strat = lambda spec: model_weights(model, spec, lv, rt, elig)  # noqa: E731
    print("[backtest] benchmarks")
    ew_net = run("EW", lv, rt, cfg, cm1, weights_fn=equal_weight(elig), tax=False, **kw)
    ew_tax = run("EW", lv, rt, cfg, cm1, weights_fn=equal_weight(elig), tax=True, **kw)
    n50_tax = run(bh_name, lv, rt, cfg, cm1, weights_fn=buy_hold(bh_asset, lv.columns), tax=True, **kw)
    ew_m = st.monthly(ew_net.nav)

    nets, taxed = {}, {}
    for i, spec in enumerate(specs):
        print(f"[backtest] {i + 1:2d}/{len(specs)} {spec.name}")
        nets[spec] = run(spec.name, lv, rt, cfg, cm1, weights_fn=strat(spec), tax=False, **kw)
        taxed[spec] = run(spec.name, lv, rt, cfg, cm1, weights_fn=strat(spec), tax=True, **kw)

    active_m = pd.DataFrame({s.name: st.monthly(nets[s].nav) - ew_m for s in specs}).dropna()
    rows = []
    for s in specs:
        p = st.perf(nets[s].nav, rf)
        pt = st.perf(taxed[s].nav, rf, taxed[s].after_tax_terminal)
        a = active_m[s.name]
        turnover = nets[s].trades.query("side != 'sell_tax'")["value"].sum() / 2 / nets[s].nav.mean()
        years = (nets[s].nav.index[-1] - nets[s].nav.index[0]).days / 365.25
        rows.append({"trial": s.name, "primary": s == prim, "CAGR_net": p["CAGR"], "Sharpe_net": p["Sharpe"],
                     "MaxDD": p["MaxDD"], "Calmar": p["Calmar"], "CAGR_after_tax": pt["CAGR_after_tax"],
                     "active_ann": a.mean() * 12, "active_IR": a.mean() / a.std() * np.sqrt(12),
                     "turnover_pa": turnover / years})
    trials = pd.DataFrame(rows)

    # ---- Gate 4: selection statistics ------------------------------------------------------
    trial_sr = (active_m.mean() / active_m.std()).to_numpy()
    dsr = st.deflated_sharpe(active_m[prim.name], trial_sr)
    dsr_best = st.deflated_sharpe(active_m[trials.loc[trials["active_IR"].idxmax(), "trial"]], trial_sr)
    pbo = st.pbo_cscv(active_m, cfg["evaluation"]["pbo_blocks"])
    haircut = st.haircut_sharpe(trials["active_IR"].max(), trials["active_IR"].to_numpy())
    boot_p = st.block_bootstrap_p(active_m[prim.name], cfg["evaluation"]["bootstrap_draws"])
    robustness = float((trials["active_IR"] > 0).mean())
    selection_ok = bool(dsr["dsr"] > 0.95 and pbo["pbo"] < 0.5)
    print(f"[selection gate] {'PASS' if selection_ok else 'FAIL'}  DSR={dsr['dsr']:.3f} PBO={pbo['pbo']:.2f}")

    # ---- Walk-forward selection (trial 25) -------------------------------------------------
    wf_cfg = cfg["evaluation"]["walk_forward_selection"]
    wf = {}
    for kind in ("expanding", "rolling"):
        picks, out = [], []
        years = sorted(set(active_m.index.year))
        for y in years:
            hist = active_m[active_m.index.year < y]
            if kind == "rolling":
                hist = hist[hist.index.year >= y - wf_cfg["min_train_years"]]
            if hist.index.year.nunique() < wf_cfg["min_train_years"]:
                continue
            best = (hist.mean() / hist.std()).idxmax()
            picks.append((y, best))
            out.append(active_m.loc[active_m.index.year == y, best])
        s_ = pd.concat(out)
        wf[kind] = {"series": s_, "picks": picks, "active_ann": s_.mean() * 12,
                    "active_IR": s_.mean() / s_.std() * np.sqrt(12)}
    oos_from = wf["expanding"]["series"].index[0]
    prim_same_window = active_m.loc[oos_from:, prim.name]

    # ---- Gate 5: costs ----------------------------------------------------------------------
    cost_rows = []
    for m in cfg["evaluation"]["cost_multipliers"]:
        cm = make_cost_model(cfg, panel, m)
        r_s = nets[prim] if m == 1 else run(prim.name, lv, rt, cfg, cm, weights_fn=strat(prim), tax=False, **kw)
        r_b = ew_net if m == 1 else run("EW", lv, rt, cfg, cm, weights_fn=equal_weight(elig), tax=False, **kw)
        ps, pb = st.perf(r_s.nav, rf), st.perf(r_b.nav, rf)
        act = st.monthly(r_s.nav) - st.monthly(r_b.nav)
        cost_rows.append({"cost_x": m, "CAGR": ps["CAGR"], "Sharpe": ps["Sharpe"], "EW_CAGR": pb["CAGR"],
                          "active_ann": act.mean() * 12, "cost_drag_pa": annual_cost_drag(r_s)})
    costs = pd.DataFrame(cost_rows)
    gross_active = costs.loc[costs["cost_x"] == 0, "active_ann"].iloc[0]
    drag = costs.loc[costs["cost_x"] == 1, "cost_drag_pa"].iloc[0]
    ew_drag = annual_cost_drag(ew_net)
    safety = gross_active / max(drag - ew_drag, 1e-9) if gross_active > 0 else 0.0
    safety_total = gross_active / drag if drag > 0 and gross_active > 0 else 0.0
    active_3x = costs.loc[costs["cost_x"] == 3, "active_ann"].iloc[0]
    cost_ok = bool(safety_total >= 2.5 and active_3x > 0)
    cap = capacity_inr(nets[prim], panel, cfg)
    cost_split = nets[prim].trades.query("side != 'sell_tax'")[["explicit", "spread", "impact"]].sum()

    # ---- Gate 3 and tax gate ---------------------------------------------------------------
    pp = st.perf(nets[prim].nav, rf)
    pe = st.perf(ew_net.nav, rf)
    ppt = st.perf(taxed[prim].nav, rf, taxed[prim].after_tax_terminal)
    pet = st.perf(ew_tax.nav, rf, ew_tax.after_tax_terminal)
    pnt = st.perf(n50_tax.nav, rf, n50_tax.after_tax_terminal)
    strategy_ok = bool(pp["CAGR"] > pe["CAGR"] and pp["Sharpe"] > 0.5 and pp["MaxDD"] >= pe["MaxDD"])
    tax_ok = bool(ppt["CAGR_after_tax"] > pet["CAGR_after_tax"])
    m30_cagr = np.nan
    if ref_series is not None:
        m30 = ref_series.loc[nets[prim].nav.index[0]:end].dropna()
        m30_cagr = (m30.iloc[-1] / m30.iloc[0]) ** (365.25 / (m30.index[-1] - m30.index[0]).days) - 1

    # ---- Figures -----------------------------------------------------------------------------
    navs = pd.DataFrame({"Strategy (net)": nets[prim].nav, "EW (net)": ew_net.nav,
                         f"{bh_name} (buy & hold)": n50_tax.nav})
    navs = navs / navs.iloc[0]
    fig, ax = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]})
    navs.plot(ax=ax[0], logy=True, lw=1.3)
    ax[0].set_title(f"Development period, pre-tax, net of costs — primary {prim.name}")
    (navs / navs.cummax() - 1).plot(ax=ax[1], lw=1, legend=False)
    ax[1].set_ylabel("drawdown")
    fig.tight_layout(); fig.savefig(FIG / "dev_equity_drawdown.png", dpi=130); plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 3.5))
    ic_prim.plot(ax=ax, lw=0.6, alpha=0.6, label="rank IC per decision")
    ic_prim.rolling(24).mean().plot(ax=ax, lw=1.8, label="24-month mean")
    ax.axhline(0, color="k", lw=0.6); ax.legend(); ax.set_title(f"Rank IC, {model.signal_key(prim)}")
    fig.tight_layout(); fig.savefig(FIG / "dev_ic.png", dpi=130); plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
    for ax_, score in zip(axes, cfg["grid"]["score"]):
        sub = trials[trials["trial"].str.startswith(score + "_")].copy()
        sub["lookback"] = sub["trial"].str.extract(r"_(\d+(?:-\d+)?)_top")[0]
        sub["k"] = sub["trial"].str.extract(r"top(\d+)")[0].astype(int)
        hm = sub.pivot(index="lookback", columns="k", values="active_IR")
        im = ax_.imshow(hm.values, cmap="RdBu", vmin=-1, vmax=1)
        ax_.set_xticks(range(len(hm.columns)), hm.columns); ax_.set_yticks(range(len(hm.index)), hm.index)
        for (i_, j_), v in np.ndenumerate(hm.values):
            ax_.text(j_, i_, f"{v:.2f}", ha="center", va="center", fontsize=9)
        ax_.set_title(f"active IR — score={score}"); ax_.set_xlabel("top k")
    fig.colorbar(im, ax=axes, shrink=0.8)
    fig.savefig(FIG / "dev_sensitivity.png", dpi=130); plt.close(fig)

    rolling = (active_m[prim.name].rolling(36).mean() / active_m[prim.name].rolling(36).std() * np.sqrt(12))
    fig, ax = plt.subplots(figsize=(10, 3.2))
    rolling.plot(ax=ax); ax.axhline(0, color="k", lw=0.6); ax.set_title("Rolling 36-month active IR vs EW")
    fig.tight_layout(); fig.savefig(FIG / "dev_rolling_active_ir.png", dpi=130); plt.close(fig)

    # ---- Report --------------------------------------------------------------------------
    gates = [
        ("Data", data_ok, "see data_validation.md"),
        ("Signal", signal_ok, f"IC {prow['mean_IC']:.3f}, NW t {prow['t_NW']:.2f}, halves "
                              f"{prow['IC_1st_half']:.3f} / {prow['IC_2nd_half']:.3f}"),
        ("Strategy", strategy_ok, f"CAGR {pct(pp['CAGR'])} vs EW {pct(pe['CAGR'])}; Sharpe {num(pp['Sharpe'])}; "
                                  f"MaxDD {pct(pp['MaxDD'])} vs EW {pct(pe['MaxDD'])}"),
        ("Selection", selection_ok, f"DSR {dsr['dsr']:.3f} (need > 0.95); PBO {pbo['pbo']:.2f} (need < 0.50)"),
        ("Costs", cost_ok, f"safety margin {safety_total:.1f}x (need >= 2.5x); active at 3x costs {pct(active_3x, 2)}"),
        ("Tax", tax_ok, f"after-tax CAGR {pct(ppt['CAGR_after_tax'])} vs EW {pct(pet['CAGR_after_tax'])}"),
    ]
    killed = (not signal_ok) or (dsr["dsr"] <= 0.95 and pbo["pbo"] >= 0.5)

    perf_tbl = pd.DataFrame({
        "Primary (net)": pp, "EW (net)": pe,
        "Primary (after tax)": ppt, "EW (after tax)": pet, f"{bh_name} B&H (after tax)": pnt,
    }).T
    fmt_pct = ["CAGR", "Vol", "MaxDD", "VaR95_d", "CVaR95_d", "Win_m", "CAGR_after_tax"]
    perf_fmt = perf_tbl.copy().astype(object)
    for c in perf_tbl.columns:
        perf_fmt[c] = [pct(v) if c in fmt_pct else (f"{v:.0f}" if c == "MaxDD_days" else num(v)) for v in perf_tbl[c]]

    tr_fmt = trials.copy()
    for c in ["CAGR_net", "MaxDD", "CAGR_after_tax", "active_ann"]:
        tr_fmt[c] = tr_fmt[c].map(pct)
    for c in ["Sharpe_net", "Calmar", "active_IR", "turnover_pa"]:
        tr_fmt[c] = tr_fmt[c].map(num)
    ic_fmt = ic_table.copy()
    for c in ic_fmt.columns[1:]:
        ic_fmt[c] = ic_fmt[c].map(lambda v: f"{v:.3f}" if isinstance(v, float) else v)
    cost_fmt = costs.copy()
    for c in ["CAGR", "EW_CAGR", "active_ann", "cost_drag_pa"]:
        cost_fmt[c] = cost_fmt[c].map(lambda v: pct(v, 2))
    cost_fmt["Sharpe"] = cost_fmt["Sharpe"].map(num)

    last_w = nets[prim].weights.iloc[-1]
    lines = [
        "# Development-period report", "",
        f"Primary spec: **{prim.name}**. Period: {start.date()} to {dev_end.date()} "
        f"({len(active_m)} months). Holdout (from {cfg['periods']['holdout_start']}) not touched.", "",
        "## Gates (pre-registered in HYPOTHESIS.md)", "",
        "| Gate | Result | Evidence |", "|---|---|---|",
        *[f"| {g} | {'PASS' if ok else 'FAIL'} | {ev} |" for g, ok, ev in gates],
        "",
        f"**Kill criterion triggered: {'YES — strategy rejected' if killed else 'no'}**",
        "",
    ]
    if suspicious_ic:
        lines += ["> Mean IC above 0.15: treat as a probable bug or lookahead, not as success.", ""]
    lines += [
        "## Performance (development period)", "",
        "Sharpe and Sortino are on returns in excess of the NIFTY 1D rate (6% p.a. before it exists). "
        "After-tax CAGR includes tax paid each April and tax on liquidating the book on the last day.", "",
        perf_fmt.to_markdown(), "",
        f"Reference {ref_name} (price series, no costs/tax): CAGR {pct(m30_cagr)} over the same window." if ref_name else "", "",
        "![equity](figures/dev_equity_drawdown.png)", "",
        "## Signal research (monthly cross-sectional rank IC, development period)", "",
        "IC is Spearman correlation between the score at the decision close and the return from the next "
        "execution close to the following one. `t_NW` uses Newey-West errors "
        f"({lag} lags). `IC_hm` is the mean IC against the h-month forward return (decay).", "",
        ic_fmt.to_markdown(index=False), "",
        "![ic](figures/dev_ic.png)", "",
        f"## All {len(specs)} trials (net of costs; active = vs EW, net)", "",
        tr_fmt.to_markdown(index=False), "",
        "![sensitivity](figures/dev_sensitivity.png)", "",
        f"Robustness: {robustness:.0%} of the grid has positive active IR (sensitivity-analysis skill targets > 60%).", "",
        "## Selection statistics", "",
        f"- Deflated Sharpe Ratio, primary spec, monthly active returns, N = {len(specs)}: **{dsr['dsr']:.3f}** "
        f"(per-month SR {dsr['sr']:.3f} vs expected max under null {dsr['e_max_sr']:.3f}; skew {dsr['skew']:.2f}, "
        f"kurtosis {dsr['kurtosis']:.2f}; n = {dsr['n']}).",
        f"- Probability the primary active return is > 0 ignoring selection (PSR): {dsr['psr_vs_0']:.3f}; "
        f"block-bootstrap one-sided p-value: {boot_p:.3f}.",
        f"- DSR of the best grid cell: {dsr_best['dsr']:.3f}.",
        f"- Sharpe haircut on the best annualised active IR ({trials['active_IR'].max():.2f}): {haircut:+.2f} after the selection bound.",
        f"- PBO (CSCV, {cfg['evaluation']['pbo_blocks']} blocks, {pbo['n_splits']} splits): **{pbo['pbo']:.2f}**; "
        f"in-sample-best per-month SR {pbo['is_sr_best_mean']:.3f} -> out-of-sample {pbo['oos_sr_best_mean']:.3f}.",
        "",
        "## Walk-forward selection (trial 25)", "",
        "Each January, pick the grid cell with the best trailing active Sharpe and trade it for the year "
        "(stitched monthly active returns; switching costs between cells are ignored).", "",
        "| Variant | Active p.a. | Active IR | Primary, same window |", "|---|---|---|---|",
        *[f"| {k} | {pct(v['active_ann'], 2)} | {num(v['active_IR'])} | "
          f"{pct(prim_same_window.mean() * 12, 2)} / IR {num(prim_same_window.mean() / prim_same_window.std() * np.sqrt(12))} |"
          for k, v in wf.items()],
        "",
        "Picks (expanding): " + ", ".join(f"{y}: {s}" for y, s in wf["expanding"]["picks"]), "",
        "![rolling](figures/dev_rolling_active_ir.png)", "",
        "## Costs, capacity", "",
        cost_fmt.to_markdown(index=False), "",
        f"- Safety margin (gross active return / strategy cost drag): **{safety_total:.1f}x**; "
        f"versus the extra cost over EW only: {safety:.1f}x.",
        f"- Cost split at 1x (rupees over the period): explicit {cost_split['explicit']:,.0f}, "
        f"spread {cost_split['spread']:,.0f}, impact {cost_split['impact']:,.0f}.",
        f"- Capacity (95th-percentile trade at <= {cfg['costs']['max_participation']:.0%} of current ETF ADV): "
        f"about **Rs {cap / 1e7:,.1f} crore**.",
        "",
        "## Latest development-period holdings", "",
        ", ".join(f"{a} {w:.0%}" for a, w in last_w[last_w > 0].items()), "",
    ]
    (OUT / "REPORT_dev.md").write_text("\n".join(lines) + "\n")

    trial_lines = ["# Trials log", "",
                   f"Pre-registered grid: {len(specs)} trials + walk-forward selection = {len(specs) + 1}.",
                   "Any rerun with a changed specification must be appended here.", "",
                   "| # | Trial | Source |", "|---|---|---|"]
    trial_lines += [f"| {i + 1} | {s.name} | grid{' (primary)' if s == prim else ''} |" for i, s in enumerate(specs)]
    trial_lines += [f"| {len(specs) + 1} | walk-forward selection | grid |"]
    (OUT / "TRIALS.md").write_text("\n".join(trial_lines) + "\n")

    # ---- Artifacts for the dashboard -----------------------------------------------------
    art = OUT / "artifacts" / "dev"
    art.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"primary_net": nets[prim].nav, "primary_after_tax": taxed[prim].nav,
                  "ew_net": ew_net.nav, "ew_after_tax": ew_tax.nav, "market_after_tax": n50_tax.nav,
                  "reference_price": (ref_series.reindex(nets[prim].nav.index) if ref_series is not None
                                      else np.nan),
                  "rf": rf.reindex(nets[prim].nav.index)}).to_parquet(art / "nav.parquet")
    pd.DataFrame({s.name: nets[s].nav for s in specs}).to_parquet(art / "trial_nav.parquet")
    nets[prim].weights.to_parquet(art / "weights.parquet")
    nets[prim].trades.to_parquet(art / "trades.parquet")
    taxed[prim].trades.to_parquet(art / "trades_after_tax.parquet")
    pd.DataFrame({"costs": nets[prim].costs}).to_parquet(art / "costs.parquet")
    taxed[prim].tax_paid.rename("tax").to_frame().to_parquet(art / "tax_paid.parquet")
    ew_tax.tax_paid.rename("tax").to_frame().to_parquet(art / "ew_tax_paid.parquet")
    trials.to_parquet(art / "trials.parquet")
    ic_table.to_parquet(art / "ic_table.parquet")
    ic_prim.rename("ic").to_frame().to_parquet(art / "ic_series.parquet")
    active_m.to_parquet(art / "active_monthly.parquet")
    costs.to_parquet(art / "cost_sensitivity.parquet")
    pd.DataFrame({k: v["series"] for k, v in wf.items()}).to_parquet(art / "walk_forward.parquet")
    (art / "stats.json").write_text(json.dumps({
        "strategy_id": strategy_id, "model": cfg["model"],
        "benchmark_names": {"market": bh_name, "reference": ref_name},
        "primary": prim.name, "start": str(start.date()), "end": str(dev_end.date()),
        "months": len(active_m), "n_trials": len(specs),
        "gates": [{"gate": g, "pass": ok, "evidence": ev} for g, ok, ev in gates], "killed": killed,
        "perf": {k: {m: (None if pd.isna(v) else float(v)) for m, v in d.items()}
                 for k, d in {"primary_net": pp, "ew_net": pe, "primary_after_tax": ppt,
                              "ew_after_tax": pet, "market_after_tax": pnt}.items()},
        "reference_cagr": m30_cagr,
        "dsr": dsr, "dsr_best": dsr_best, "pbo": pbo, "haircut": haircut, "bootstrap_p": boot_p,
        "robustness": robustness,
        "walk_forward": {k: {"active_ann": v["active_ann"], "active_IR": v["active_IR"],
                             "picks": v["picks"]} for k, v in wf.items()},
        "primary_same_window": {"active_ann": prim_same_window.mean() * 12,
                                "active_IR": prim_same_window.mean() / prim_same_window.std() * np.sqrt(12)},
        "safety_margin": safety_total, "safety_margin_vs_ew": safety, "active_3x": active_3x,
        "capacity_inr": cap, "cost_split": cost_split.to_dict(),
        "liquidation_tax": taxed[prim].liquidation_tax, "ew_liquidation_tax": ew_tax.liquidation_tax,
        "suspicious_ic": suspicious_ic,
    }, indent=2, default=lambda o: float(o) if isinstance(o, (np.floating, np.integer)) else str(o)))

    summary = {"start": str(start.date()), "dev_end": str(dev_end.date()),
               "gates": {g: ok for g, ok, _ in gates}, "killed": killed,
               "dsr": dsr["dsr"], "pbo": pbo["pbo"], "ic": float(prow["mean_IC"]), "ic_t": float(prow["t_NW"]),
               "safety_margin": safety_total, "capacity_inr": cap}
    (OUT / "dev_summary.json").write_text(json.dumps(summary, indent=2))
    print("\n".join(f"  {g:10s} {'PASS' if ok else 'FAIL'}  {ev}" for g, ok, ev in gates))
    print(f"  killed: {killed}\n-> {OUT / 'REPORT_dev.md'}")


if __name__ == "__main__":
    import sys
    main(sys.argv[1])
