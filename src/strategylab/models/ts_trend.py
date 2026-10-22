"""Time-series trend (absolute momentum): each risky asset gets 1/N of the book
while its own trend is positive; the weight of every asset whose trend is not
positive goes to a defensive asset (cash or government bonds)."""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import pandas as pd

SIGNAL_TEST = "time_series"


@dataclass(frozen=True)
class Spec:
    rule: str        # "excess" | "sma"
    lookback: int
    defensive: str   # asset id receiving the weight of risky assets that are off

    @property
    def name(self) -> str:
        return f"{signal_key(self)}_to{self.defensive}"


def signal_key(spec: Spec) -> str:
    return f"{spec.rule}_{spec.lookback}"


def grid(cfg: dict) -> list[Spec]:
    g = cfg["grid"]
    return [Spec(r, lb, d) for r, lb, d in itertools.product(g["rule"], g["lookback"], g["defensive"])]


def primary(cfg: dict) -> Spec:
    p = cfg["primary"]
    return Spec(p["rule"], p["lookback"], p["defensive"])


def risky(cfg: dict) -> list[str]:
    return [a["id"] for a in cfg["universe"]["assets"] if a.get("role", "risky") == "risky"]


def signal(levels: pd.DataFrame, returns: pd.DataFrame, spec: Spec, cfg: dict) -> pd.DataFrame:
    """Positive = trend on. Defined for risky assets only (NaN elsewhere). Reads levels <= t."""
    cols = risky(cfg)
    lv = levels[cols]
    if spec.rule == "excess":
        cash = levels[cfg["universe"]["cash_asset"]]
        cash_ret = cash / cash.shift(spec.lookback) - 1
        out = (lv / lv.shift(spec.lookback) - 1).sub(cash_ret, axis=0)
    elif spec.rule == "sma":
        out = lv / lv.rolling(spec.lookback, min_periods=spec.lookback).mean() - 1
    else:
        raise ValueError(spec.rule)
    return out.reindex(columns=levels.columns)


def weights(signal_row: pd.Series, eligible_row: pd.Series, spec: Spec, cfg: dict) -> pd.Series:
    w = pd.Series(0.0, index=signal_row.index)
    live = signal_row[risky(cfg)].where(eligible_row[risky(cfg)]).dropna()
    if live.empty:
        return w
    share = 1.0 / len(live)
    on = live.index[live > 0]
    w[on] = share
    off_weight = share * (len(live) - len(on))
    if off_weight > 0 and eligible_row.get(spec.defensive, False):
        w[spec.defensive] += off_weight
    return w  # if the defensive asset is not yet eligible, its share stays uninvested
