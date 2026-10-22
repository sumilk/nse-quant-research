"""Current ranking for the next rebalance (operational view, not research evidence).

Scores every asset with the strategy's primary spec as of the latest close and
lists what it would hold if the decision were taken on that close.
"""

from __future__ import annotations

import json

import pandas as pd

from strategylab.config import load_config, results_dir
from strategylab.models import load_model
from strategylab.data.panel import build_panel
from strategylab.engine.schedule import eligibility


def main(strategy_id: str) -> None:
    cfg = load_config(strategy_id)
    model = load_model(cfg["model"])
    panel = build_panel(cfg)
    spec = model.primary(cfg)
    lv, rt = panel.levels, panel.returns
    asof = lv.index[-1]
    sc = model.signal(lv, rt, spec, cfg).loc[asof]
    el = eligibility(lv, rt, cfg["universe"]["min_history_sessions"]).loc[asof]
    w = model.weights(sc, el, spec, cfg)
    etf = {a["id"]: a.get("etf") for a in cfg["universe"]["assets"]}
    vol = rt.rolling(63).std().loc[asof] * (252 ** 0.5)
    rows = pd.DataFrame({"asset": sc.index, "etf": [etf[a] for a in sc.index], "score": sc.values,
                         "eligible": el.values, "target_weight": w.reindex(sc.index).fillna(0).values,
                         "vol_63d_ann": vol.values,
                         "ret_1m": (lv.loc[asof] / lv.shift(21).loc[asof] - 1).values,
                         "adv_cr": [float(panel.etf_adv_cr.get(a, float("nan"))) for a in sc.index]})
    rows = rows.sort_values("score", ascending=False, na_position="last")
    rows["rank"] = range(1, len(rows) + 1)
    out = results_dir(strategy_id) / "artifacts" / "current"
    out.mkdir(parents=True, exist_ok=True)
    rows.to_parquet(out / "ranking.parquet")
    (out / "meta.json").write_text(json.dumps({"asof": str(asof.date()), "spec": spec.name}))
    print(rows.to_string(index=False))
