"""Cross-sectional momentum: rank eligible assets by trailing return (or return per
unit of volatility), hold the top k in equal weight."""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd


SIGNAL_TEST = "cross_sectional"


@dataclass(frozen=True)
class Spec:
    score: str      # "ret" | "sharpe"
    lookback: int
    skip: int
    top_k: int

    @property
    def name(self) -> str:
        return f"{signal_key(self)}_top{self.top_k}"


def signal_key(spec: Spec) -> str:
    return f"{spec.score}_{spec.lookback}" + (f"-{spec.skip}" if spec.skip else "")


def grid(cfg: dict) -> list[Spec]:
    g = cfg["grid"]
    return [Spec(s, lb, sk, k) for s, (lb, sk), k in itertools.product(g["score"], g["lookback_skip"], g["top_k"])]


def primary(cfg: dict) -> Spec:
    p = cfg["primary"]
    return Spec(p["score"], p["lookback"], p["skip"], p["top_k"])


def signal(levels: pd.DataFrame, returns: pd.DataFrame, spec: Spec, cfg: dict | None = None) -> pd.DataFrame:
    end = levels.shift(spec.skip)
    start = levels.shift(spec.skip + spec.lookback)
    ret = end / start - 1
    if spec.score == "ret":
        return ret
    if spec.score == "sharpe":
        vol = returns.shift(spec.skip).rolling(spec.lookback, min_periods=spec.lookback).std()
        return ret / (vol * np.sqrt(spec.lookback))
    raise ValueError(spec.score)


def weights(signal_row: pd.Series, eligible_row: pd.Series, spec: Spec, cfg: dict | None = None) -> pd.Series:
    s = signal_row.where(eligible_row).dropna()
    chosen = s.sort_values(ascending=False).index[: spec.top_k]
    w = pd.Series(0.0, index=signal_row.index)
    if len(chosen):
        w[chosen] = 1.0 / len(chosen)
    return w
