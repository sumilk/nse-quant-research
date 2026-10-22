"""One-shot holdout evaluation (backtest-overfitting: holdout used exactly once).

Runs the pre-registered primary spec, and the walk-forward pick for the holdout
year(s), from periods.holdout_start to the last available session. Refuses to
run again once results/artifacts/holdout/USED exists.

    uv run python -m india_etf_momentum.holdout --i-have-not-changed-anything
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import numpy as np
import pandas as pd

from . import stats as st
from .backtest import run
from .config import ROOT, load_config
from .panel import build_panel
from .research import make_cost_model, primary_spec
from .signals import Spec

ART = ROOT / "results" / "artifacts" / "holdout"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--i-have-not-changed-anything", action="store_true", dest="confirm",
                    help="confirm the spec is unchanged since the development gates were evaluated")
    args = ap.parse_args()
    marker = ART / "USED"
    if marker.exists():
        sys.exit(f"Holdout already used on {marker.read_text().strip()}. Re-running it would spend it twice.")
    if not args.confirm:
        sys.exit("Pass --i-have-not-changed-anything to spend the holdout (this can be done once).")
    dev = json.loads((ROOT / "results" / "artifacts" / "dev" / "stats.json").read_text())

    cfg = load_config()
    panel = build_panel()
    start = pd.Timestamp(cfg["periods"]["holdout_start"])
    # Start one decision earlier so the first holdout trade is the 2024-09 month-end decision.
    first_decision = panel.levels.index[panel.levels.index < start][-1]
    end = panel.levels.index[-1]
    lv, rt, rf = panel.levels, panel.returns, panel.rf
    cm = make_cost_model(cfg, panel, 1.0)
    prim = primary_spec(cfg)
    kw = dict(start=first_decision, end=end)

    res = {
        "primary_net": run(prim.name, lv, rt, cfg, cm, spec=prim, tax=False, **kw),
        "primary_after_tax": run(prim.name, lv, rt, cfg, cm, spec=prim, tax=True, **kw),
        "ew_net": run("EW", lv, rt, cfg, cm, mode="equal_weight", tax=False, **kw),
        "ew_after_tax": run("EW", lv, rt, cfg, cm, mode="equal_weight", tax=True, **kw),
        "nifty50_after_tax": run("NIFTY50", lv, rt, cfg, cm, mode="buy_hold:NIFTY50", tax=True, **kw),
    }
    wf_pick = dev["walk_forward"]["expanding"]["picks"][-1][1]
    score, rest = wf_pick.split("_", 1)
    lb, k = rest.split("_top")
    lb, skip = (lb.split("-") + ["0"])[:2]
    wf_spec = Spec(score, int(lb), int(skip), int(k))
    res["walkforward_net"] = run(wf_spec.name, lv, rt, cfg, cm, spec=wf_spec, tax=False, **kw)

    perf = {}
    for k_, r in res.items():
        p = st.perf(r.nav, rf, r.after_tax_terminal if r.meta["tax"] else None)
        perf[k_] = {m: (None if pd.isna(v) else float(v)) for m, v in p.items()}
    active = st.monthly(res["primary_net"].nav) - st.monthly(res["ew_net"].nav)
    a_tax = perf["primary_after_tax"]["CAGR_after_tax"] - perf["ew_after_tax"]["CAGR_after_tax"]
    passed = bool(a_tax > 0)

    ART.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({k_: r.nav for k_, r in res.items()} | {
        "momentum30_price": panel.momentum30.reindex(res["primary_net"].nav.index),
        "rf": rf.reindex(res["primary_net"].nav.index)}).to_parquet(ART / "nav.parquet")
    res["primary_net"].weights.to_parquet(ART / "weights.parquet")
    res["primary_net"].trades.to_parquet(ART / "trades.parquet")
    (ART / "stats.json").write_text(json.dumps({
        "primary": prim.name, "walk_forward_spec": wf_spec.name,
        "start": str(res["primary_net"].nav.index[0].date()), "end": str(end.date()),
        "perf": perf, "active_monthly_mean_ann": float(active.mean() * 12),
        "active_IR": float(active.mean() / active.std() * np.sqrt(12)) if active.std() > 0 else None,
        "after_tax_active_cagr": a_tax, "pass": passed,
    }, indent=2))
    marker.write_text(datetime.now().isoformat(timespec="seconds"))
    print(f"Holdout {res['primary_net'].nav.index[0].date()}..{end.date()}: "
          f"after-tax active CAGR {a_tax:+.2%} -> {'PASS' if passed else 'FAIL'}")


if __name__ == "__main__":
    main()
