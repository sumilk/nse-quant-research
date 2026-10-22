"""Current ranking for the next rebalance (operational view, not research evidence).

Scores every asset with the primary spec as of the latest close and lists what
the strategy would hold after the next month-end decision if the month ended today.
"""

from __future__ import annotations

import json

import pandas as pd

from .config import ROOT, load_config
from .panel import build_panel
from .research import primary_spec
from .signals import eligibility, scores, target_weights


def main() -> None:
    cfg = load_config()
    panel = build_panel()
    spec = primary_spec(cfg)
    lv, rt = panel.levels, panel.returns
    asof = lv.index[-1]
    sc = scores(lv, rt, spec).loc[asof]
    el = eligibility(lv, rt, cfg["universe"]["min_history_sessions"]).loc[asof]
    w = target_weights(sc, el, spec.top_k)
    etf = {a["id"]: a["etf"] for a in cfg["universe"]["assets"]}
    vol = rt.rolling(63).std().loc[asof] * (252 ** 0.5)
    rows = pd.DataFrame({"asset": sc.index, "etf": [etf[a] for a in sc.index], "score": sc.values,
                         "eligible": el.values, "target_weight": w.values,
                         "vol_63d_ann": vol.values,
                         "ret_1m": (lv.loc[asof] / lv.shift(21).loc[asof] - 1).values,
                         "adv_cr": [float(panel.etf_adv_cr.get(a, float("nan"))) for a in sc.index]})
    rows = rows.sort_values("score", ascending=False, na_position="last")
    rows["rank"] = range(1, len(rows) + 1)
    out = ROOT / "results" / "artifacts" / "current"
    out.mkdir(parents=True, exist_ok=True)
    rows.to_parquet(out / "ranking.parquet")
    (out / "meta.json").write_text(json.dumps({"asof": str(asof.date()), "spec": spec.name}))
    print(rows.to_string(index=False))


if __name__ == "__main__":
    main()
