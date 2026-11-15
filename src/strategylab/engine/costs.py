"""Indian ETF delivery-trade costs: explicit charges + half-spread + square-root impact."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class CostModel:
    cfg: dict
    adv_inr: dict[str, float]        # median daily traded value per asset, rupees
    tax_class: dict[str, str]
    multiplier: float = 1.0          # 0 = frictionless, 2/3 = stress

    adv_history: pd.DataFrame | None = None

    def adv(self, asset: str, when=None) -> float:
        if self.adv_history is not None:
            if when is None:
                raise ValueError("A trade date is required for historical liquidity")
            value = float(self.adv_history.at[when, asset])
        else:
            value = float(self.adv_inr[asset])
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"{asset} {when}: no valid trailing liquidity")
        return value

    def half_spread_bps(self, asset: str, when=None) -> float:
        adv_cr = self.adv(asset, when) / 1e7
        for floor_cr, bps in self.cfg["half_spread_tiers"]:
            if adv_cr >= floor_cr:
                return bps
        return self.cfg["half_spread_tiers"][-1][1]

    def trade_cost(self, asset: str, value: float, side: str, daily_vol: float, when=None) -> dict[str, float]:
        """Cost in rupees of trading `value` rupees of `asset` (side: 'buy' | 'sell')."""
        if value <= 0 or self.multiplier == 0:
            return {"explicit": 0.0, "spread": 0.0, "impact": 0.0, "total": 0.0}
        c = self.cfg
        pct = lambda p: value * p / 100  # noqa: E731
        fees = c["brokerage_inr_per_order"] + pct(c["exchange_txn_pct"]) + pct(c["sebi_fee_pct"])
        explicit = fees * (1 + c["gst_pct"] / 100)
        if side == "buy":
            explicit += pct(c["stamp_duty_buy_pct"])
        else:
            if self.tax_class[asset] == "equity":
                explicit += pct(c["stt_sell_pct"])
            explicit += c["dp_charge_inr_per_sell"]
        spread = value * self.half_spread_bps(asset, when) / 1e4
        participation = value / self.adv(asset, when)
        vol = daily_vol if np.isfinite(daily_vol) else 0.0
        impact = value * c["impact_coefficient"] * vol * np.sqrt(participation)
        m = self.multiplier
        out = {"explicit": explicit * m, "spread": spread * m, "impact": impact * m}
        out["total"] = sum(out.values())
        return out
