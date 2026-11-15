"""Accounting and causal-data regressions for the shared strategy infrastructure."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from strategylab.data import fetch, panel
from strategylab.data.validate import validate
from strategylab.engine.backtest import buy_hold, run
from strategylab.engine.costs import CostModel
from strategylab.engine.schedule import panel_eligibility
from strategylab.evaluation import stats
from strategylab.evaluation.research import make_cost_model
from test_core import COST_CFG, _cfg, _panel


@pytest.fixture
def sources(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "RAW", tmp_path)
    dates = pd.bdate_range("2020-01-01", periods=400)
    for asset, first in (("A", 0), ("B", 150)):
        price = 100 * np.exp(np.arange(len(dates)) * 0.001)
        frame = pd.DataFrame({"date": dates, "open": price, "high": price,
                              "low": price, "close": price})
        frame.to_parquet(fetch.nse_path(asset))
        pd.DataFrame({"date": dates, "dy_pct": 1.0}).to_parquet(fetch.nsedy_path(asset))
        frame.assign(adj_close=price, volume=10000.0).iloc[first:].to_parquet(fetch.etf_path(asset))
    cfg = _cfg(("A", "B"))
    cfg["periods"] = {"data_start": str(dates[0].date()), "dev_end": str(dates[300].date())}
    cfg["universe"].update(calendar_master="A", cash_asset="A")
    for asset in cfg["universe"]["assets"]:
        asset.update(source="nse", index=asset["id"], etf=asset["id"])
    cfg["costs"] = {**COST_CFG, "proxy_adv_inr": 1e7}
    return cfg, dates


def test_future_raw_prices_and_volume_cannot_change_development(sources):
    cfg, dates = sources
    before = panel.build_panel(cfg, mode="etf")
    # Corrupt only the excluded tail, including a bad print that would affect
    # centred cleaning if filtering happened after preprocessing.
    for asset in ("A", "B"):
        path = fetch.etf_path(asset)
        df = pd.read_parquet(path)
        future = df.date > dates[300]
        df.loc[future, ["close", "adj_close", "volume"]] *= 1000
        df.to_parquet(path)
    after = panel.build_panel(cfg, mode="etf")
    pd.testing.assert_frame_equal(before.levels, after.levels)
    pd.testing.assert_frame_equal(before.returns, after.returns)
    pd.testing.assert_frame_equal(before.adv_history, after.adv_history)
    assert before.etf_spikes == after.etf_spikes
    assert after.calendar.max() == dates[300]


def test_etf_mode_does_not_backfill_pre_listing_index_history(sources):
    cfg, dates = sources
    actual = panel.build_panel(cfg, mode="etf")
    proxy = panel.build_panel(cfg, mode="index_proxy")
    assert actual.levels.loc[:dates[149], "B"].isna().all()
    assert proxy.levels.loc[dates[20], "B"] > 0
    eligible = panel_eligibility(actual, 20)
    assert not eligible.loc[:dates[169], "B"].any()
    assert eligible.loc[dates[180], "B"]


def test_trade_cost_uses_only_liquidity_before_that_trade(sources):
    cfg, dates = sources
    before = panel.build_panel(cfg, mode="etf")
    path = fetch.etf_path("A")
    df = pd.read_parquet(path)
    df.loc[df.date >= dates[200], "volume"] *= 100
    df.to_parquet(path)
    after = panel.build_panel(cfg, mode="etf")
    cost1 = make_cost_model(cfg, before).trade_cost("A", 100000, "buy", .01, when=dates[200])
    cost2 = make_cost_model(cfg, after).trade_cost("A", 100000, "buy", .01, when=dates[200])
    assert cost1 == cost2
    assert after.adv_history.at[dates[250], "A"] > before.adv_history.at[dates[250], "A"]


def test_unresolved_price_gaps_fail_data_gate(sources, tmp_path):
    cfg, dates = sources
    path = fetch.nse_path("B")
    df = pd.read_parquet(path)
    df = df[~df.date.isin(dates[200:207])]
    df.to_parquet(path)
    p = panel.build_panel(cfg)
    ok, report = validate(p, cfg, tmp_path / "report")
    assert not ok
    assert "unresolved valuation gaps" in report
    assert dates[202] in p.gaps["B"]


def test_missing_quote_on_execution_day_is_not_a_fill():
    lv, rt = _panel(assets=("A",), n=100)
    cfg = _cfg(lv.columns)
    cm = CostModel(COST_CFG, {"A": 1e9}, {"A": "equity"}, multiplier=0)
    observed = pd.DataFrame(True, index=lv.index, columns=lv.columns)
    observed.loc[pd.Timestamp("2015-02-02"), "A"] = False
    with pytest.raises(ValueError, match="missing/stale quote"):
        run("bh", lv, rt, cfg, cm, weights_fn=buy_hold("A", lv.columns),
            start=lv.index[0], end=lv.index[-1], tax=False, observed=observed)


def test_held_asset_with_missing_valuation_stops_backtest():
    lv, rt = _panel(assets=("A",), n=100)
    cfg = _cfg(lv.columns)
    lv.loc[lv.index[60], "A"] = np.nan
    cm = CostModel(COST_CFG, {"A": 1e9}, {"A": "equity"}, multiplier=0)
    with pytest.raises(ValueError, match="finite valuation"):
        run("bh", lv, rt, cfg, cm, weights_fn=buy_hold("A", lv.columns),
            start=lv.index[0], end=lv.index[-1], tax=False)


def test_sortino_matches_hand_calculated_downside_deviation():
    idx = pd.bdate_range("2020-01-01", periods=4)
    returns = np.array([-.01, .02, .03])
    nav = pd.Series(np.r_[100, 100 * np.cumprod(1 + returns)], index=idx)
    got = stats.perf(nav, pd.Series(0.0, index=idx))["Sortino"]
    expected = returns.mean() * 252 / np.sqrt((.01 ** 2 / 3) * 252)
    assert got == pytest.approx(expected)


@pytest.mark.parametrize("fn", [stats.perf, stats.monthly])
def test_metrics_reject_nav_gaps(fn):
    idx = pd.bdate_range("2020-01-01", periods=4)
    nav = pd.Series([100, 101, np.nan, 103], index=idx)
    with pytest.raises(ValueError, match="without gaps"):
        fn(nav, pd.Series(0., index=idx)) if fn is stats.perf else fn(nav)


def test_tax_funding_is_self_financing_and_uses_actual_sale_date():
    idx = pd.bdate_range("2023-12-01", "2025-04-10")
    lv = pd.DataFrame({"A": np.linspace(100, 250, len(idx)), "B": 100.}, index=idx)
    rt = lv.pct_change()
    cfg = _cfg(lv.columns)
    costs = {**COST_CFG, "dp_charge_inr_per_sell": 1000.0}
    cm = CostModel(costs, {"A": 1e7, "B": 1e7}, {"A": "equity", "B": "equity"})
    def weights(d):
        # Realize taxable gains, then reinvest the whole book before FY end.
        return pd.Series({"A": 0., "B": 1.}) if d.month == 2 else pd.Series({"A": 1., "B": 0.})
    res = run("tax", lv, rt, cfg, cm, weights_fn=weights, start=idx[0], end=idx[-1])
    assert len(res.tax_paid) > 0
    assert (res.cash >= 0).all()
    np.testing.assert_allclose(res.cash + res.invested, res.nav)
    assert (res.tax_paid.index.month == 4).all()
    funding = res.trades[res.trades.side == "sell_tax"]
    assert len(funding) > 0
    assert (funding.date.dt.month == 4).all()
    signed = res.trades.value.where(res.trades.side != "buy", -res.trades.value) - res.trades.total
    flows = signed.groupby(res.trades.date).sum().reindex(idx, fill_value=0)
    cash = res.initial + flows.cumsum() - res.tax_paid.reindex(idx, fill_value=0).cumsum()
    np.testing.assert_allclose(res.cash, cash, atol=1e-7)


def test_future_prices_inside_development_do_not_rewrite_past_features(sources):
    cfg, dates = sources
    before = panel.build_panel(cfg, mode="etf")
    path = fetch.etf_path("A")
    df = pd.read_parquet(path)
    df.loc[df.date > dates[200], ["close", "adj_close"]] *= 100
    df.to_parquet(path)
    after = panel.build_panel(cfg, mode="etf")
    pd.testing.assert_frame_equal(before.levels.loc[:dates[200]], after.levels.loc[:dates[200]])
    pd.testing.assert_frame_equal(before.adv_history.loc[:dates[200]], after.adv_history.loc[:dates[200]])


def test_development_pipeline_writes_reconciled_artifacts(tmp_path, monkeypatch):
    from strategylab.evaluation import research
    lv, rt = _panel(n=2100, assets=("A", "B", "C", "D", "E", "F", "G", "CASH"))
    cfg = _cfg(lv.columns)
    cfg.update(strategy_id="synthetic", model="xsec_momentum",
               periods={"data_start": str(lv.index[0].date()), "dev_end": str(lv.index[-1].date()),
                        "holdout_start": "2030-01-01"},
               primary={"score": "ret", "lookback": 5, "skip": 0, "top_k": 2},
               grid={"score": ["ret", "sharpe"], "lookback_skip": [[5, 0]], "top_k": [2]},
               benchmarks={"market": "A"}, costs={**COST_CFG, "proxy_adv_inr": 1e7},
               evaluation={"pbo_blocks": 4, "bootstrap_draws": 20, "newey_west_lags": 3,
                           "cost_multipliers": [0, 1, 2, 3],
                           "walk_forward_selection": {"min_train_years": 5}})
    cfg["universe"].update(min_history_sessions=5, cash_asset="CASH", calendar_master="A")
    for a in cfg["universe"]["assets"]:
        a.update(source="yahoo", etf=a["id"])
    etfs = {a: pd.DataFrame({"adj_close": lv[a], "close": lv[a], "volume": 1e7}, index=lv.index)
            for a in lv}
    p = panel.Panel(lv.index, rt, lv, pd.Series(.0001, index=lv.index), {}, {},
                    dict.fromkeys(lv.columns, 0), etfs, {}, {},
                    pd.Series(1., index=lv.columns), {},
                    observed=lv.notna(), mode="index_proxy", cutoff=lv.index[-1])
    monkeypatch.setattr(research, "load_config", lambda sid: deepcopy(cfg))
    monkeypatch.setattr(research, "results_dir", lambda sid: tmp_path)
    monkeypatch.setattr(research, "build_panel", lambda *args, **kwargs: p)
    research.main("synthetic")
    import json
    assert json.loads((tmp_path / "validation_status.json").read_text())["state"] == "valid"
    cash = pd.read_parquet(tmp_path / "artifacts/dev/cash.parquet")
    assert (cash >= 0).all().all()
    nav = pd.read_parquet(tmp_path / "artifacts/dev/nav.parquet")
    assert np.isfinite(nav.drop(columns="reference_price")).all().all()


def test_failed_rerun_invalidates_previous_results(tmp_path, monkeypatch):
    from strategylab.evaluation import research
    import json
    cfg = {"periods": {"dev_end": "2024-09-30"}}
    (tmp_path / "REPORT_dev.md").write_text("Old report")
    monkeypatch.setattr(research, "load_config", lambda sid: cfg)
    monkeypatch.setattr(research, "results_dir", lambda sid: tmp_path)
    def fail(*args):
        raise ValueError("Unresolved source gap")
    monkeypatch.setattr(research, "_research", fail)
    with pytest.raises(SystemExit, match="source gap"):
        research.main("test")
    assert json.loads((tmp_path / "validation_status.json").read_text())["state"] == "blocked"
    assert (tmp_path / "legacy_review/REPORT_dev.md").read_text() == "Old report"
    assert "No corrected performance estimate" in (tmp_path / "REPORT_dev.md").read_text()


def test_blocked_report_from_corrected_run_is_not_snapshotted_as_legacy(tmp_path, monkeypatch):
    from strategylab.evaluation import research
    monkeypatch.setattr(research, "load_config", lambda sid: {"periods": {"dev_end": "2024-09-30"}})
    monkeypatch.setattr(research, "results_dir", lambda sid: tmp_path)
    def fail(*args):
        raise ValueError("Unresolved source gap")
    monkeypatch.setattr(research, "_research", fail)
    for _ in range(2):
        with pytest.raises(SystemExit):
            research.main("test")
    assert not (tmp_path / "legacy_review").exists()


def test_unexpected_error_still_blocks_and_replaces_report(tmp_path, monkeypatch):
    from strategylab.evaluation import research
    import json
    (tmp_path / "REPORT_dev.md").write_text("Old report")
    monkeypatch.setattr(research, "load_config", lambda sid: {"periods": {"dev_end": "2024-09-30"}})
    monkeypatch.setattr(research, "results_dir", lambda sid: tmp_path)
    def fail(*args):
        raise KeyError("CASH")
    monkeypatch.setattr(research, "_research", fail)
    with pytest.raises(SystemExit, match="KeyError"):
        research.main("test")
    assert json.loads((tmp_path / "validation_status.json").read_text())["state"] == "blocked"
    assert "No corrected performance estimate" in (tmp_path / "REPORT_dev.md").read_text()
