"""Daily event loop shared by every strategy.

- A strategy supplies a weights function: decision date -> target weights.
- Weights decided at the close of decision date d fill at the close of the
  execution session (d + lag sessions). Between those closes the old book earns
  the return.
- Positions are held as FIFO lots (units, cost, buy date) for tax.
- Sells execute before buys; buys are scaled so cash never goes negative.
- Prior-year tax is funded and paid at the first session of the next FY.
  Funding sales are recorded on their actual execution dates.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType

import pandas as pd
import numpy as np

from strategylab.engine.costs import CostModel
from strategylab.engine.schedule import decision_schedule
from strategylab.engine.tax import TaxBook, financial_year

WeightsFn = Callable[[pd.Timestamp], "pd.Series | None"]


def model_weights(model: ModuleType, spec, levels: pd.DataFrame, returns: pd.DataFrame,
                  elig: pd.DataFrame, cfg: dict) -> WeightsFn:
    sig = model.signal(levels, returns, spec, cfg)
    return lambda d: model.weights(sig.loc[d], elig.loc[d], spec, cfg)


def equal_weight(elig: pd.DataFrame) -> WeightsFn:
    def fn(d):
        e = elig.loc[d]
        return e.astype(float) / e.sum() if e.sum() else None
    return fn


def buy_hold(asset: str, columns: pd.Index) -> WeightsFn:
    state = {"done": False}

    def fn(d):
        if state["done"]:
            return None
        state["done"] = True
        return pd.Series(0.0, index=columns).where(columns != asset, 1.0)
    return fn


@dataclass
class Result:
    name: str
    nav: pd.Series                     # daily NAV, rupees (pre-liquidation)
    weights: pd.DataFrame              # target weights by execution date
    trades: pd.DataFrame
    costs: pd.Series                   # daily cost, rupees
    tax_paid: pd.Series                # by payment date
    liquidation_tax: float             # tax if the book were sold on the last day
    initial: float
    meta: dict = field(default_factory=dict)
    cash: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    invested: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))

    @property
    def after_tax_terminal(self) -> float:
        return float(self.nav.iloc[-1] - self.liquidation_tax)


class Book:
    def __init__(self, cash: float):
        self.cash = cash
        self.lots: dict[str, deque] = {}

    def units(self, a: str) -> float:
        return sum(l[1] for l in self.lots.get(a, ()))

    def value(self, prices: pd.Series) -> float:
        return self.cash + sum(self.units(a) * prices[a] for a in self.lots)

    def buy(self, a: str, units: float, price: float, when: pd.Timestamp) -> None:
        if units > 0:
            self.lots.setdefault(a, deque()).append([when, units, price])

    def sell(self, a: str, units: float, price: float, when: pd.Timestamp) -> list[tuple[float, int]]:
        """FIFO sale; returns realized (gain, held_days) per lot touched."""
        out, q = [], self.lots.get(a, deque())
        while units > 1e-12 and q:
            lot = q[0]
            take = min(units, lot[1])
            out.append((take * (price - lot[2]), (when - lot[0]).days))
            lot[1] -= take
            units -= take
            if lot[1] <= 1e-12:
                q.popleft()
        if not q:
            self.lots.pop(a, None)
        return out

    def unrealized(self, prices: pd.Series, when: pd.Timestamp) -> list[tuple[str, float, int]]:
        return [(a, l[1] * (prices[a] - l[2]), (when - l[0]).days)
                for a, q in self.lots.items() for l in q]


def run(
    name: str,
    levels: pd.DataFrame,
    returns: pd.DataFrame,
    cfg: dict,
    cost_model: CostModel,
    *,
    weights_fn: WeightsFn,           # decision date -> target weights (None = no trade)
    start: pd.Timestamp,
    end: pd.Timestamp,
    tax: bool = True,
    meta: dict | None = None,
    observed: pd.DataFrame | None = None,
) -> Result:
    cal = levels.index[(levels.index >= start) & (levels.index <= end)]
    sched = decision_schedule(levels.index, cfg["decision"]["execution_lag_sessions"], cfg["decision"]["cadence"])
    sched = sched[(sched["decision"] >= start) & (sched["execution"] <= end)]
    exec_to_dec = dict(zip(sched["execution"], sched["decision"]))
    if len(cal) < 2:
        raise ValueError("Backtest requires at least two sessions")
    vol = returns.rolling(63, min_periods=21).std().shift(1)
    fresh = levels.notna() if observed is None else observed.reindex_like(levels).fillna(False)
    tax_class = {a["id"]: a["tax_class"] for a in cfg["universe"]["assets"]}
    book_tax = TaxBook(cfg["tax"], tax_class)

    initial = float(cfg["portfolio"]["initial_capital_inr"])
    book = Book(initial)
    nav, cost_s, w_log, trades, tax_paid = {}, {}, {}, [], {}
    cash_path, invested_path = {}, {}

    def check_cash(when):
        if not np.isfinite(book.cash) or book.cash < -1e-7:
            raise ValueError(f"{when}: cash constraint violated ({book.cash})")
        book.cash = max(0.0, book.cash)

    def require_quotes(assets, when, prices):
        for asset in assets:
            if not fresh.at[when, asset] or not np.isfinite(prices[asset]) or prices[asset] <= 0:
                raise ValueError(f"{when} {asset}: cannot execute on a missing/stale quote")

    def sell(asset, value, when, prices, side="sell"):
        require_quotes([asset], when, prices)
        c = cost_model.trade_cost(asset, value, "sell", vol.at[when, asset], when=when)
        for gain, held in book.sell(asset, value / prices[asset], prices[asset], when):
            book_tax.record(when, asset, gain, held)
        book.cash += value - c["total"]
        check_cash(when)
        trades.append({"date": when, "asset": asset, "side": side, "value": value, **c})
        return c["total"]


    for i, t in enumerate(cal):
        px = levels.loc[t]
        day_cost = 0.0
        if not np.isfinite(book.value(px)):
            raise ValueError(f"{t}: held asset has no finite valuation; repair source gaps")

        # Pay last FY's settled liability at today's prices, before new buys.
        if tax and i > 0 and financial_year(t) != financial_year(cal[i - 1]):
            due = book_tax.settle(financial_year(cal[i - 1]))
            need = max(due - book.cash, 0.0)
            if need > 0:
                values = {a: book.units(a) * px[a] for a in book.lots
                          if book.units(a) * px[a] > 1 and fresh.at[t, a]}
                require_quotes(values, t, px)
                def proceeds(frac):
                    return sum(max(0.0, v * frac - cost_model.trade_cost(
                        a, v * frac, "sell", vol.at[t, a], when=t)["total"])
                        for a, v in values.items())
                if proceeds(1.0) < need:
                    raise ValueError(f"{t}: insufficient net proceeds to fund tax")
                lo, hi = 0.0, 1.0
                for _ in range(50):
                    mid = (lo + hi) / 2
                    if proceeds(mid) >= need:
                        hi = mid
                    else:
                        lo = mid
                for a, value in values.items():
                    gross = value * hi
                    fee = cost_model.trade_cost(a, gross, "sell", vol.at[t, a], when=t)["total"]
                    if gross > fee:
                        day_cost += sell(a, gross, t, px, "sell_tax")
            book.cash -= due
            check_cash(t)
            if due > 0:
                tax_paid[t] = due

        if t in exec_to_dec:
            w = weights_fn(exec_to_dec[t])
            if w is not None:
                w = w.reindex(levels.columns).fillna(0.0)
                if not np.isfinite(w).all() or (w < 0).any() or w.sum() > 1 + 1e-9:
                    raise ValueError("Target weights must be finite, long-only and unlevered")
                w_log[t] = w
                total = book.value(px)
                cur = pd.Series({a: book.units(a) * px[a] if book.units(a) else 0.0 for a in levels.columns})
                delta = w * total - cur
                # Sells first.
                sales = -delta[delta < -1]
                # Larger net proceeds fund fixed charges on small rebalance sells.
                require_quotes(sales.index, t, px)
                sale_net = {a: value - cost_model.trade_cost(
                    a, value, "sell", vol.at[t, a], when=t)["total"] for a, value in sales.items()}
                if book.cash + sum(sale_net.values()) < -1e-7:
                    raise ValueError(f"{t}: insufficient cash for rebalance sale charges")
                for a in sorted(sale_net, key=sale_net.get, reverse=True):
                    day_cost += sell(a, sales[a], t, px)
                # Buys, scaled to the cash actually available after costs.
                buys = delta[delta > 1]
                if len(buys):
                    require_quotes(buys.index, t, px)
                    est = {a: cost_model.trade_cost(a, buys[a], "buy", vol.at[t, a], when=t)["total"] for a in buys.index}
                    need = buys.sum() + sum(est.values())
                    scale = min(1.0, book.cash / need) if need > 0 else 0.0
                    def purchase_cost(fraction):
                        return sum(v * fraction + cost_model.trade_cost(
                            a, v * fraction, "buy", vol.at[t, a], when=t)["total"]
                            for a, v in buys.items())
                    if purchase_cost(scale) > book.cash:
                        lo, hi = 0.0, scale
                        for _ in range(50):
                            mid = (lo + hi) / 2
                            if purchase_cost(mid) <= book.cash:
                                lo = mid
                            else:
                                hi = mid
                        scale = lo
                    for a in buys.index:
                        value = buys[a] * scale
                        if value <= 1e-9:
                            continue
                        c = cost_model.trade_cost(a, value, "buy", vol.at[t, a], when=t)
                        book.buy(a, value / px[a], px[a], t)
                        book.cash -= value + c["total"]
                        check_cash(t)
                        day_cost += c["total"]
                        trades.append({"date": t, "asset": a, "side": "buy", "value": value, **c})

        check_cash(t)
        nav[t] = book.value(px)
        if not np.isfinite(nav[t]) or nav[t] <= 0:
            raise ValueError(f"{t}: invalid portfolio NAV")
        cash_path[t] = book.cash
        invested_path[t] = nav[t] - book.cash
        cost_s[t] = day_cost

    last = cal[-1]
    liq_tax = 0.0
    if tax:
        liq_tax = book_tax.preview(financial_year(last), book.unrealized(levels.loc[last], last))

    return Result(
        name=name,
        nav=pd.Series(nav, name=name),
        weights=pd.DataFrame(w_log).T.reindex(columns=levels.columns).fillna(0.0),
        trades=pd.DataFrame(trades),
        costs=pd.Series(cost_s),
        tax_paid=pd.Series(tax_paid, dtype=float),
        liquidation_tax=liq_tax,
        initial=initial,
        cash=pd.Series(cash_path), invested=pd.Series(invested_path),
        meta={**(meta or {}), "tax": tax, "cost_multiplier": cost_model.multiplier},
    )
