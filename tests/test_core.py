import numpy as np
import pandas as pd
import pytest

from strategylab.engine.backtest import buy_hold, model_weights, run
from strategylab.engine.costs import CostModel
from strategylab.engine.schedule import decision_schedule, eligibility
from strategylab.engine.tax import TaxBook
from strategylab.models import xsec_momentum as xm

TAX_CFG = {
    "cess_pct": 4.0, "slab_rate_pct": 30.0,
    "equity": {"st_rate": 20.0, "lt_rate": 12.5, "lt_months": 12, "lt_exemption_inr": 125000},
    "gold_silver": {"st_rate": "slab", "lt_rate": 12.5, "lt_months": 12, "lt_exemption_inr": 0},
    "debt": {"st_rate": "slab", "lt_rate": "slab", "lt_months": None, "lt_exemption_inr": 0},
}
COST_CFG = {
    "brokerage_inr_per_order": 0.0, "stt_sell_pct": 0.001, "exchange_txn_pct": 0.00297,
    "sebi_fee_pct": 0.0001, "stamp_duty_buy_pct": 0.015, "gst_pct": 18.0, "dp_charge_inr_per_sell": 16.0,
    "half_spread_tiers": [[100, 2], [20, 5], [5, 10], [0, 20]], "impact_coefficient": 0.1,
    "max_participation": 0.05,
}


def _panel(n=800, assets=("A", "B", "C", "D", "E", "F"), seed=0):
    rng = np.random.default_rng(seed)
    cal = pd.bdate_range("2015-01-01", periods=n)
    r = pd.DataFrame(rng.normal(0.0004, 0.01, (n, len(assets))), index=cal, columns=list(assets))
    r.iloc[0] = np.nan
    lv = (1 + r.fillna(0)).cumprod()
    return lv, r


def _cfg(assets):
    return {
        "decision": {"execution_lag_sessions": 1, "cadence": "monthly_last_session"},
        "universe": {"min_history_sessions": 252,
                     "assets": [{"id": a, "tax_class": "equity"} for a in assets]},
        "tax": TAX_CFG, "portfolio": {"initial_capital_inr": 1_000_000},
    }


def test_scores_ignore_future_prices():
    lv, r = _panel()
    spec = xm.Spec("sharpe", 126, 21, 3)
    d = lv.index[500]
    base = xm.signal(lv, r, spec).loc[d]
    lv2, r2 = lv.copy(), r.copy()
    r2.iloc[501:] *= -5
    lv2.iloc[501:] *= 3
    assert xm.signal(lv2, r2, spec).loc[d].equals(base)


def test_execution_is_after_decision_and_month_end():
    cal = pd.bdate_range("2020-01-01", "2020-12-31")
    s = decision_schedule(cal, 1)
    assert (s["execution"] > s["decision"]).all()
    assert (s["decision"].dt.month != (s["decision"] + pd.offsets.BDay(1)).dt.month).all()


def test_top_k_respects_eligibility():
    score = pd.Series({"A": 5.0, "B": 4.0, "C": 3.0})
    elig = pd.Series({"A": False, "B": True, "C": True})
    w = xm.weights(score, elig, xm.Spec("ret", 126, 0, 1))
    assert w["B"] == 1.0 and w.sum() == 1.0


def test_tax_set_off_rules():
    book = TaxBook(TAX_CFG, {"EQ": "equity", "GOLD": "gold_silver"})
    when = pd.Timestamp("2025-06-01")
    book.record(when, "EQ", 200_000, 400)     # equity LTCG
    book.record(when, "EQ", -50_000, 100)     # equity STCL offsets LT
    book.record(when, "GOLD", 10_000, 100)    # gold short term at slab
    # ST loss goes to the highest-rate bucket first: gold slab 30% -> 10k, then eq LT 40k.
    tax = book.settle(2025)
    expected = ((200_000 - 40_000 - 125_000) * 0.125) * 1.04
    assert tax == pytest.approx(expected)


def test_long_term_loss_cannot_offset_short_term():
    book = TaxBook(TAX_CFG, {"EQ": "equity"})
    when = pd.Timestamp("2025-06-01")
    book.record(when, "EQ", -100_000, 500)
    book.record(when, "EQ", 100_000, 30)
    assert book.settle(2025) == pytest.approx(100_000 * 0.20 * 1.04)
    assert book.lt_loss_cf == pytest.approx(100_000)


def test_costs_components():
    cm = CostModel(COST_CFG, {"A": 50e7}, {"A": "equity"})
    buy = cm.trade_cost("A", 100_000, "buy", 0.01)
    sell = cm.trade_cost("A", 100_000, "sell", 0.01)
    assert buy["spread"] == pytest.approx(100_000 * 5 / 1e4)
    assert sell["explicit"] > buy["explicit"] - 15 + 16 - 1  # DP charge on sells
    assert CostModel(COST_CFG, {"A": 50e7}, {"A": "equity"}, multiplier=0).trade_cost("A", 1e5, "buy", 0.01)["total"] == 0


def test_frictionless_buy_hold_matches_asset_return():
    lv, r = _panel()
    cfg = _cfg(lv.columns)
    cm = CostModel(COST_CFG, {a: 1e9 for a in lv.columns}, {a: "equity" for a in lv.columns}, multiplier=0)
    res = run("bh", lv, r, cfg, cm, weights_fn=buy_hold("A", lv.columns), start=lv.index[300], end=lv.index[-1],
              tax=False)
    first_exec = res.weights.index[0]
    got = res.nav.iloc[-1] / res.nav.loc[first_exec]
    want = lv["A"].iloc[-1] / lv["A"].loc[first_exec]
    assert got == pytest.approx(want, rel=1e-9)


def test_cash_never_negative_with_costs_and_tax():
    lv, r = _panel(n=1300)
    cfg = _cfg(lv.columns)
    cm = CostModel(COST_CFG, {a: 2e7 for a in lv.columns}, {a: "equity" for a in lv.columns})
    elig = eligibility(lv, r, 252)
    wf = model_weights(xm, xm.Spec("ret", 126, 0, 2), lv, r, elig, cfg)
    res = run("rk", lv, r, cfg, cm, weights_fn=wf, start=lv.index[260], end=lv.index[-1])
    assert (res.nav > 0).all()
    assert res.costs.sum() > 0


def test_every_strategy_config_is_consistent():
    from strategylab.config import list_strategies, load_config, strategy_dir
    from strategylab.models import load_model

    for sid in list_strategies():
        cfg = load_config(sid)
        model = load_model(cfg["model"])
        assert model.primary(cfg) in model.grid(cfg), f"{sid}: primary spec not in grid"
        assert (strategy_dir(sid) / "HYPOTHESIS.md").exists(), f"{sid}: no pre-registration"
        ids = {a["id"] for a in cfg["universe"]["assets"]}
        assert cfg["universe"]["calendar_master"] in ids


def _trend_cfg(assets):
    cfg = _cfg(assets)
    for a in cfg["universe"]["assets"]:
        a["role"] = "defensive" if a["id"] in ("CASH", "BOND") else "risky"
    cfg["universe"]["cash_asset"] = "CASH"
    return cfg


def test_trend_signal_ignores_future_and_defensive_assets():
    from strategylab.models import ts_trend as tt
    lv, r = _panel(assets=("A", "B", "C", "CASH", "BOND"))
    cfg = _trend_cfg(lv.columns)
    for rule in ("excess", "sma"):
        spec = tt.Spec(rule, 126, "CASH")
        d = lv.index[500]
        base = tt.signal(lv, r, spec, cfg)
        assert base[["CASH", "BOND"]].isna().all().all()
        lv2 = lv.copy()
        lv2.iloc[501:] *= 2
        assert tt.signal(lv2, r, spec, cfg).loc[d].equals(base.loc[d])


def test_trend_weights_route_off_assets_to_defensive():
    from strategylab.models import ts_trend as tt
    cols = pd.Index(["A", "B", "C", "D", "CASH", "BOND"])
    cfg = _trend_cfg(cols)
    sig = pd.Series({"A": 0.1, "B": -0.2, "C": 0.05, "D": np.nan, "CASH": np.nan, "BOND": np.nan})
    elig = pd.Series(True, index=cols)
    w = tt.weights(sig, elig, tt.Spec("excess", 252, "BOND"), cfg)
    assert w["A"] == pytest.approx(1 / 3) and w["C"] == pytest.approx(1 / 3)
    assert w["B"] == 0 and w["D"] == 0 and w["CASH"] == 0
    assert w["BOND"] == pytest.approx(1 / 3)
    assert w.sum() == pytest.approx(1.0)


def test_rebase_is_undone_and_spike_is_removed():
    from strategylab.data.panel import _adjust_rebases, _drop_spikes
    idx = pd.bdate_range("2020-01-01", periods=40)
    close = pd.Series(np.linspace(10, 11, 40), index=idx)
    close.iloc[20:] *= 100          # permanent x100 face-value change
    close.iloc[10] /= 10            # one-off bad print
    px, spikes = _drop_spikes(pd.DataFrame({"close": close}))
    px, events = _adjust_rebases(px)
    assert spikes == [idx[10]]
    assert len(events) == 1 and events[0][1] == pytest.approx(100, rel=0.01)
    assert px["close"].pct_change().abs().max() < 0.01
