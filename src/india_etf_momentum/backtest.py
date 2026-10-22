"""Daily event loop for the monthly rotation.

- Weights decided at the close of decision date d fill at the close of the
  execution session (d + 1 session). Between those closes the old book earns
  the return.
- Positions are held as FIFO lots (units, cost, buy date) for tax.
- Sells execute before buys; buys are scaled so cash never goes negative.
- Tax for each financial year is paid at its last session by selling the book
  pro rata (that sale is itself taxable in the next year).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .costs import CostModel
from .signals import Spec, decision_schedule, eligibility, scores, target_weights
from .tax import TaxBook, financial_year


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
    spec: Spec | None = None,
    mode: str = "rank",              # "rank" | "equal_weight" | "buy_hold:<asset>"
    start: pd.Timestamp,
    end: pd.Timestamp,
    tax: bool = True,
) -> Result:
    cal = levels.index[(levels.index >= start) & (levels.index <= end)]
    lag = cfg["decision"]["execution_lag_sessions"]
    sched = decision_schedule(levels.index, lag)
    sched = sched[(sched["decision"] >= start) & (sched["execution"] <= end)]
    exec_to_dec = dict(zip(sched["execution"], sched["decision"]))

    elig = eligibility(levels, returns, cfg["universe"]["min_history_sessions"])
    sc = scores(levels, returns, spec) if spec is not None else None
    vol = returns.rolling(63, min_periods=21).std()
    tax_class = {a["id"]: a["tax_class"] for a in cfg["universe"]["assets"]}
    book_tax = TaxBook(cfg["tax"], tax_class)

    initial = float(cfg["portfolio"]["initial_capital_inr"])
    book = Book(initial)
    nav, cost_s, w_log, trades, tax_paid = {}, {}, {}, [], {}
    bought_once = False

    for i, t in enumerate(cal):
        px = levels.loc[t]
        day_cost = 0.0

        if t in exec_to_dec:
            d = exec_to_dec[t]
            if mode == "rank":
                w = target_weights(sc.loc[d], elig.loc[d], spec.top_k)
            elif mode == "equal_weight":
                w = target_weights(pd.Series(0.0, index=levels.columns), elig.loc[d], None)
            elif mode.startswith("buy_hold:"):
                asset = mode.split(":", 1)[1]
                w = None if bought_once else pd.Series(0.0, index=levels.columns).where(
                    levels.columns != asset, 1.0)
            else:
                raise ValueError(mode)

            if w is not None and w.sum() > 0:
                bought_once = True
                w_log[t] = w
                total = book.value(px)
                cur = pd.Series({a: book.units(a) * px[a] for a in levels.columns})
                delta = w * total - cur
                # Sells first.
                for a in delta[delta < -1].index:
                    value = -delta[a]
                    c = cost_model.trade_cost(a, value, "sell", vol.at[t, a])
                    for gain, held in book.sell(a, value / px[a], px[a], t):
                        book_tax.record(t, a, gain, held)
                    book.cash += value - c["total"]
                    day_cost += c["total"]
                    trades.append({"date": t, "asset": a, "side": "sell", "value": value, **c})
                # Buys, scaled to the cash actually available after costs.
                buys = delta[delta > 1]
                if len(buys):
                    est = {a: cost_model.trade_cost(a, buys[a], "buy", vol.at[t, a])["total"] for a in buys.index}
                    need = buys.sum() + sum(est.values())
                    scale = min(1.0, book.cash / need) if need > 0 else 0.0
                    for a in buys.index:
                        value = buys[a] * scale
                        c = cost_model.trade_cost(a, value, "buy", vol.at[t, a])
                        book.buy(a, value / px[a], px[a], t)
                        book.cash -= value + c["total"]
                        day_cost += c["total"]
                        trades.append({"date": t, "asset": a, "side": "buy", "value": value, **c})

        # Financial-year end: pay the year's tax by selling the book pro rata.
        is_fy_end = i + 1 == len(cal) or financial_year(cal[i + 1]) != financial_year(t)
        if tax and is_fy_end and i + 1 < len(cal):
            due = book_tax.settle(financial_year(t))
            if due > 0:
                tax_paid[t] = due
                need = max(due - book.cash, 0.0)
                book.cash -= due
                invested = book.value(px) - book.cash
                if need > 0 and invested > 0:
                    frac = min(need * 1.002 / invested, 1.0)  # small gross-up covers sale costs
                    for a in list(book.lots):
                        value = book.units(a) * px[a] * frac
                        c = cost_model.trade_cost(a, value, "sell", vol.at[t, a])
                        for gain, held in book.sell(a, value / px[a], px[a], t):
                            # Sold on the FY's last day: realized in the next FY.
                            book_tax.record(cal[i + 1], a, gain, held)
                        book.cash += value - c["total"]
                        day_cost += c["total"]
                        trades.append({"date": t, "asset": a, "side": "sell_tax", "value": value, **c})

        nav[t] = book.value(px)
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
        meta={"spec": spec, "mode": mode, "tax": tax, "cost_multiplier": cost_model.multiplier},
    )
