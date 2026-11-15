"""Strategy-independent timing: the decision schedule and point-in-time eligibility.

Weights decided at the close of decision date d are filled at the close of the
session `lag` sessions later, so the decision-day close is information, never a
fill price.
"""

from __future__ import annotations

import pandas as pd

CADENCES = {
    "monthly_last_session": lambda c: [c.year, c.month],
    "weekly_last_session": lambda c: [c.isocalendar().year.values, c.isocalendar().week.values],
    "quarterly_last_session": lambda c: [c.year, c.quarter],
}


def decision_schedule(calendar: pd.DatetimeIndex, lag: int = 1,
                      cadence: str = "monthly_last_session") -> pd.DataFrame:
    """Last session of each period -> execution session `lag` sessions later."""
    s = pd.Series(calendar, index=calendar)
    decisions = s.groupby(CADENCES[cadence](calendar)).max().sort_values().values
    pos = calendar.get_indexer(decisions)
    keep = pos + lag < len(calendar)
    return pd.DataFrame({"decision": calendar[pos[keep]], "execution": calendar[pos[keep] + lag]})


def eligibility(levels: pd.DataFrame, returns: pd.DataFrame, min_history: int) -> pd.DataFrame:
    """True where the asset has a price today and >= min_history past returns."""
    history = returns.notna().cumsum()
    return levels.notna() & (history >= min_history)


def panel_eligibility(panel, min_history: int) -> pd.DataFrame:
    """Common strategy/benchmark eligibility; never trade a carried quote."""
    out = eligibility(panel.levels, panel.returns, min_history)
    out &= panel.observed.reindex_like(out).fillna(False)
    if panel.mode == "etf":
        out &= panel.adv_history.reindex_like(out).gt(0)
    return out
