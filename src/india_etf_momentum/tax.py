"""Capital-gains tax for a resident individual, rules in force after 23-Jul-2024.

Buckets per financial year (April-March):
  eq_st  equity ETF held <= 12 months          20%
  eq_lt  equity ETF held  > 12 months          12.5% above Rs 1.25 lakh exemption
  oth_st gold/silver <= 12 months, all debt    slab rate
  oth_lt gold/silver  > 12 months              12.5%

Set-off: a net short-term loss can offset any gain (applied to the highest-rate
bucket first); a net long-term loss can offset long-term gains only. Unused
losses carry forward (the 8-year expiry is ignored). Cess applies on top.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


def financial_year(ts: pd.Timestamp) -> int:
    return ts.year if ts.month >= 4 else ts.year - 1


@dataclass
class TaxBook:
    cfg: dict
    tax_class: dict[str, str]
    st_loss_cf: float = 0.0
    lt_loss_cf: float = 0.0
    realized: dict[int, dict[str, float]] = field(default_factory=dict)
    paid: dict[int, float] = field(default_factory=dict)

    def _rate(self, r) -> float:
        return self.cfg["slab_rate_pct"] if r == "slab" else float(r)

    def bucket(self, asset: str, held_days: int) -> str:
        cls = self.tax_class[asset]
        if cls == "debt":
            return "oth_st"
        lt = held_days > 365
        if cls == "equity":
            return "eq_lt" if lt else "eq_st"
        return "oth_lt" if lt else "oth_st"

    def record(self, when: pd.Timestamp, asset: str, gain: float, held_days: int) -> None:
        fy = financial_year(when)
        b = self.realized.setdefault(fy, {"eq_st": 0.0, "eq_lt": 0.0, "oth_st": 0.0, "oth_lt": 0.0})
        b[self.bucket(asset, held_days)] += gain

    def liability(self, gains: dict[str, float], st_cf: float, lt_cf: float) -> tuple[float, float, float]:
        """Tax due for one year's buckets; returns (tax, new st carry-forward, new lt carry-forward)."""
        c = self.cfg
        rates = {
            "eq_st": c["equity"]["st_rate"], "eq_lt": c["equity"]["lt_rate"],
            "oth_st": self._rate(c["gold_silver"]["st_rate"]), "oth_lt": c["gold_silver"]["lt_rate"],
        }
        rates = {k: self._rate(v) for k, v in rates.items()}
        g = dict(gains)
        st_loss = st_cf + sum(-g[k] for k in ("eq_st", "oth_st") if g[k] < 0)
        lt_loss = lt_cf + sum(-g[k] for k in ("eq_lt", "oth_lt") if g[k] < 0)
        for k in g:
            g[k] = max(g[k], 0.0)
        # Long-term losses: only against long-term gains, highest rate first.
        for k in sorted(("eq_lt", "oth_lt"), key=lambda k: -rates[k]):
            use = min(lt_loss, g[k]); g[k] -= use; lt_loss -= use
        # Short-term losses: against anything, highest rate first.
        for k in sorted(g, key=lambda k: -rates[k]):
            use = min(st_loss, g[k]); g[k] -= use; st_loss -= use
        g["eq_lt"] = max(g["eq_lt"] - c["equity"]["lt_exemption_inr"], 0.0)
        tax = sum(g[k] * rates[k] / 100 for k in g) * (1 + c["cess_pct"] / 100)
        return tax, st_loss, lt_loss

    def settle(self, fy: int) -> float:
        gains = self.realized.get(fy, {"eq_st": 0.0, "eq_lt": 0.0, "oth_st": 0.0, "oth_lt": 0.0})
        tax, self.st_loss_cf, self.lt_loss_cf = self.liability(gains, self.st_loss_cf, self.lt_loss_cf)
        self.paid[fy] = tax
        return tax

    def preview(self, fy: int, extra: list[tuple[str, float, int]]) -> float:
        """Tax for fy if the extra (asset, gain, held_days) disposals were added; state unchanged."""
        gains = dict(self.realized.get(fy, {"eq_st": 0.0, "eq_lt": 0.0, "oth_st": 0.0, "oth_lt": 0.0}))
        for asset, gain, held in extra:
            gains[self.bucket(asset, held)] += gain
        tax, _, _ = self.liability(gains, self.st_loss_cf, self.lt_loss_cf)
        return tax
