"""Decision schedule, point-in-time eligibility and momentum scores.

Every quantity for decision date d reads levels/returns at sessions <= d only.
Trades execute at the close of the session after d (execution_lag_sessions=1),
so the decision-day close is information, never a fill price.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Spec:
    score: str      # "ret" | "sharpe"
    lookback: int
    skip: int
    top_k: int

    @property
    def name(self) -> str:
        skip = f"-{self.skip}" if self.skip else ""
        return f"{self.score}_{self.lookback}{skip}_top{self.top_k}"


def decision_schedule(calendar: pd.DatetimeIndex, lag: int = 1) -> pd.DataFrame:
    """Last session of each month -> execution session `lag` sessions later."""
    s = pd.Series(calendar, index=calendar)
    decisions = s.groupby([calendar.year, calendar.month]).max().values
    pos = calendar.get_indexer(decisions)
    keep = pos + lag < len(calendar)
    return pd.DataFrame({"decision": calendar[pos[keep]], "execution": calendar[pos[keep] + lag]})


def eligibility(levels: pd.DataFrame, returns: pd.DataFrame, min_history: int) -> pd.DataFrame:
    """True where the asset has a price today and >= min_history past returns."""
    history = returns.notna().cumsum()
    return levels.notna() & (history >= min_history)


def scores(levels: pd.DataFrame, returns: pd.DataFrame, spec: Spec) -> pd.DataFrame:
    end = levels.shift(spec.skip)
    start = levels.shift(spec.skip + spec.lookback)
    ret = end / start - 1
    if spec.score == "ret":
        return ret
    if spec.score == "sharpe":
        vol = returns.shift(spec.skip).rolling(spec.lookback, min_periods=spec.lookback).std()
        return ret / (vol * np.sqrt(spec.lookback))
    raise ValueError(spec.score)


def target_weights(score_row: pd.Series, eligible_row: pd.Series, top_k: int | None) -> pd.Series:
    """Equal weight in the top_k eligible assets (top_k=None: all eligible)."""
    s = score_row.where(eligible_row).dropna()
    chosen = s.index if top_k is None else s.sort_values(ascending=False).index[:top_k]
    w = pd.Series(0.0, index=score_row.index)
    if len(chosen):
        w[chosen] = 1.0 / len(chosen)
    return w
