"""One-shot holdout evaluation (backtest-overfitting: holdout used exactly once).

Runs the strategy's pre-registered primary spec, and the walk-forward pick from
the development period, from periods.holdout_start to the last available
session. Refuses to run again once <strategy>/results/artifacts/holdout/USED exists.

    uv run lab holdout --strategy <id> --i-have-not-changed-anything
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

import numpy as np
import pandas as pd

from strategylab.evaluation import stats as st
from strategylab.engine.backtest import buy_hold, equal_weight, model_weights, run
from strategylab.config import load_config, results_dir
from strategylab.models import load_model
from strategylab.data.panel import build_panel
from strategylab.evaluation.research import benchmarks, make_cost_model
from strategylab.engine.schedule import panel_eligibility


def main(strategy_id: str, confirm: bool) -> None:
    art = results_dir(strategy_id) / "artifacts" / "holdout"
    marker = art / "USED"
    if marker.exists():
        sys.exit(f"Holdout already used on {marker.read_text().strip()}. Re-running it would spend it twice.")
    if not confirm:
        sys.exit("Pass --i-have-not-changed-anything to spend the holdout (this can be done once).")
    status = results_dir(strategy_id) / "validation_status.json"
    if not status.exists() or json.loads(status.read_text()).get("state") != "valid":
        sys.exit("A valid corrected development evaluation is required before holdout.")
    print("Disclosure: this window previously influenced liquidity/validation; it is not pristine.")
    dev_stats = results_dir(strategy_id) / "artifacts" / "dev" / "stats.json"
    if not dev_stats.exists():
        sys.exit("Run the development research first: every gate must be reviewed before the holdout.")
    dev = json.loads(dev_stats.read_text())

    cfg = load_config(strategy_id)
    model = load_model(cfg["model"])
    panel = build_panel(cfg, cutoff=pd.Timestamp.now().normalize())
    lv, rt, rf = panel.levels, panel.returns, panel.rf
    start = pd.Timestamp(cfg["periods"]["holdout_start"])
    # Start one session earlier so the decision taken on the last pre-holdout session is the first trade.
    first = lv.index[lv.index < start][-1]
    end = lv.index[-1]
    elig = panel_eligibility(panel, cfg["universe"]["min_history_sessions"])
    ew_elig = elig
    if cfg["evaluation"].get("ew_universe", "all") == "risky":
        risky = [a["id"] for a in cfg["universe"]["assets"] if a.get("role", "risky") == "risky"]
        ew_elig = elig & pd.Series(elig.columns.isin(risky), index=elig.columns)
    g = cfg.get("gates", {"type": "relative_return"})
    cm = make_cost_model(cfg, panel, 1.0)
    prim = model.primary(cfg)
    wf_name = dev["walk_forward"]["expanding"]["picks"][-1][1]
    wf_spec = next(s for s in model.grid(cfg) if s.name == wf_name)
    bh_name, bh_asset = benchmarks(cfg)
    kw = dict(start=first, end=end, observed=panel.observed)

    res = {
        "primary_net": run(prim.name, lv, rt, cfg, cm, weights_fn=model_weights(model, prim, lv, rt, elig, cfg), tax=False, **kw),
        "primary_after_tax": run(prim.name, lv, rt, cfg, cm, weights_fn=model_weights(model, prim, lv, rt, elig, cfg), tax=True, **kw),
        "ew_net": run("EW", lv, rt, cfg, cm, weights_fn=equal_weight(ew_elig), tax=False, **kw),
        "ew_after_tax": run("EW", lv, rt, cfg, cm, weights_fn=equal_weight(ew_elig), tax=True, **kw),
        "market_after_tax": run(bh_name, lv, rt, cfg, cm, weights_fn=buy_hold(bh_asset, lv.columns), tax=True, **kw),
        "walkforward_net": run(wf_spec.name, lv, rt, cfg, cm, weights_fn=model_weights(model, wf_spec, lv, rt, elig, cfg),
                               tax=False, **kw),
    }

    perf = {}
    for k, r in res.items():
        p = st.perf(r.nav, rf, r.after_tax_terminal if r.meta["tax"] else None)
        perf[k] = {m: (None if pd.isna(v) else float(v)) for m, v in p.items()}
    active = st.monthly(res["primary_net"].nav) - st.monthly(res["ew_net"].nav)
    a_tax = perf["primary_after_tax"]["CAGR_after_tax"] - perf["ew_after_tax"]["CAGR_after_tax"]
    if g["type"] == "relative_return":
        passed = bool(a_tax > 0)
        rule = "after-tax CAGR above EW's"
    else:
        dd_s, dd_e = perf["primary_after_tax"]["MaxDD"], perf["ew_after_tax"]["MaxDD"]
        passed = bool(dd_s >= dd_e and a_tax >= -g["tax_cagr_tolerance_pp"] / 100)
        rule = f"after-tax MaxDD no deeper than EW's and CAGR within {g['tax_cagr_tolerance_pp']} pp of EW's"

    art.mkdir(parents=True, exist_ok=True)
    ref = next(iter(panel.reference.values()), None)
    idx = res["primary_net"].nav.index
    pd.DataFrame({k: r.nav for k, r in res.items()} | {
        "reference_price": ref.reindex(idx) if ref is not None else np.nan,
        "rf": rf.reindex(idx)}).to_parquet(art / "nav.parquet")
    res["primary_net"].weights.to_parquet(art / "weights.parquet")
    res["primary_net"].trades.to_parquet(art / "trades.parquet")
    (art / "stats.json").write_text(json.dumps({
        "holdout_status": "previously_contaminated",
        "primary": prim.name, "walk_forward_spec": wf_spec.name,
        "start": str(idx[0].date()), "end": str(end.date()), "perf": perf,
        "active_monthly_mean_ann": float(active.mean() * 12),
        "active_IR": float(active.mean() / active.std() * np.sqrt(12)) if active.std() > 0 else None,
        "after_tax_active_cagr": a_tax, "pass_rule": rule, "pass": passed,
    }, indent=2))
    marker.write_text(datetime.now().isoformat(timespec="seconds"))
    print(f"[{strategy_id}] holdout {idx[0].date()}..{end.date()}: after-tax active CAGR {a_tax:+.2%} "
          f"-> {'PASS' if passed else 'FAIL'}")
