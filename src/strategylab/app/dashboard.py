"""Strategy dashboard. Reads strategies/<id>/results/artifacts written by the
research, holdout and current stages.

    uv run streamlit run src/strategylab/app/dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from strategylab.config import list_strategies, results_dir, strategy_dir

# Validated categorical order (dataviz reference palette): colour follows the entity.
# Market and reference benchmark labels are added per strategy below.
COLORS = {"Strategy": "#2a78d6", "EW": "#eb6834", "Walk-forward": "#e87ba4"}
MARKET_COLOR, REFERENCE_COLOR = "#1baf7a", "#eda100"
STRATEGY_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
DIVERGING = [[0, "#d03b3b"], [0.5, "#f0efec"], [1, "#2a78d6"]]  # red <- gray -> blue
GOOD, BAD = "✅ PASS", "❌ FAIL"

st.set_page_config(page_title="India Strategy Lab", page_icon="📈", layout="wide")


# ----- loading ------------------------------------------------------------------------------

def load(strategy_id: str, stage: str) -> dict | None:
    """Artifacts for one strategy and stage; re-read whenever any artifact file changes."""
    d = results_dir(strategy_id) / "artifacts" / stage
    if not (d / "stats.json").exists():
        return None
    stamp = max(f.stat().st_mtime for f in d.iterdir())
    return _load(str(d), stamp)


@st.cache_data
def _load(path: str, stamp: float) -> dict:
    d = Path(path)
    out = {"stats": json.loads((d / "stats.json").read_text())}
    for f in d.glob("*.parquet"):
        out[f.stem] = pd.read_parquet(f)
    return out


def pct(v, d=1):
    return "–" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{d}%}"


def num(v, d=2):
    return "–" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{d}f}"


def series_frame(nav: pd.DataFrame, after_tax: bool) -> pd.DataFrame:
    cols = {"Strategy": "primary_after_tax" if after_tax else "primary_net",
            "EW": "ew_after_tax" if after_tax else "ew_net",
            MARKET: "market_after_tax", REFERENCE: "reference_price"}
    if "walkforward_net" in nav and not after_tax:
        cols["Walk-forward"] = "walkforward_net"
    df = pd.DataFrame({k: nav[v] for k, v in cols.items() if v in nav}).dropna(how="all").dropna(axis=1, how="all")
    return df / df.bfill().iloc[0]


def perf_table(df: pd.DataFrame, rf: pd.Series) -> pd.DataFrame:
    rows = {}
    for c in df:
        nav = df[c].dropna()
        r = nav.pct_change().dropna()
        ex = r - rf.reindex(r.index).fillna(0)
        yrs = (nav.index[-1] - nav.index[0]).days / 365.25
        dd = nav / nav.cummax() - 1
        cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1
        down = ex[ex < 0].std() * np.sqrt(252)
        var = np.percentile(r, 5)
        m = nav.resample("ME").last().pct_change().dropna()
        rows[c] = {"CAGR": pct(cagr), "Volatility": pct(r.std() * np.sqrt(252)),
                   "Sharpe": num(ex.mean() / ex.std() * np.sqrt(252)),
                   "Sortino": num(ex.mean() * 252 / down if down else np.nan),
                   "Max drawdown": pct(dd.min()), "Calmar": num(cagr / abs(dd.min()) if dd.min() else np.nan),
                   "VaR 95% (daily)": pct(var, 2), "CVaR 95% (daily)": pct(r[r <= var].mean(), 2),
                   "Best month": pct(m.max()), "Worst month": pct(m.min()), "Positive months": pct((m > 0).mean(), 0),
                   "Final multiple": f"{nav.iloc[-1] / nav.iloc[0]:.2f}x"}
    return pd.DataFrame(rows)


def line_chart(df: pd.DataFrame, title: str, log=False, yfmt=".2f", height=380) -> go.Figure:
    fig = go.Figure()
    for c in df:
        fig.add_trace(go.Scatter(x=df.index, y=df[c], name=c, mode="lines",
                                 line=dict(width=2, color=COLORS.get(c)),
                                 hovertemplate=f"%{{y:{yfmt}}}"))
    fig.update_layout(title=title, height=height, hovermode="x unified", margin=dict(l=10, r=10, t=50, b=10),
                      legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom"))
    fig.update_yaxes(type="log" if log else "linear", tickformat=yfmt)
    return fig


def gate_badges(gates: list[dict]) -> None:
    cols = st.columns(len(gates))
    for col, g in zip(cols, gates):
        with col:
            st.markdown(f"**{g['gate']}**  \n{GOOD if g['pass'] else BAD}")
            st.caption(g["evidence"])


# ----- strategy picker and cross-strategy comparison -----------------------------------------

strategies = list_strategies()
with st.sidebar:
    st.header("India Strategy Lab")
    choice = st.selectbox("Strategy", ["Compare all"] + strategies,
                          index=1 if len(strategies) == 1 else 0)

if choice == "Compare all":
    st.title("Strategy comparison")
    rows, curves = [], {}
    for sid in strategies:
        d = load(sid, "dev")
        h = load(sid, "holdout")
        if d is None:
            rows.append({"strategy": sid, "status": "not researched"})
            continue
        ss = d["stats"]
        p = ss["perf"]
        rows.append({
            "strategy": sid, "model": ss.get("model"), "gates": ss.get("gate_type", "relative_return"),
            "primary": ss["primary"],
            "status": "rejected" if ss["killed"] else "alive",
            "gates passed": f"{sum(g['pass'] for g in ss['gates'])}/{len(ss['gates'])}",
            "CAGR net": pct(p["primary_net"]["CAGR"]), "CAGR after tax": pct(p["primary_after_tax"].get("CAGR_after_tax")),
            "EW CAGR": pct(p["ew_net"]["CAGR"]), "Sharpe": num(p["primary_net"]["Sharpe"]),
            "Max DD": pct(p["primary_net"]["MaxDD"]), "DSR": num(ss["dsr"]["dsr"], 3), "PBO": num(ss["pbo"]["pbo"]),
            "trials": ss["n_trials"], "holdout": ("—" if h is None else (GOOD if h["stats"]["pass"] else BAD)),
            "period": f"{ss['start']} → {ss['end']}",
        })
        curves[sid] = d["nav"]["primary_net"]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.caption("Development-period figures, net of costs. Each strategy is judged against its own "
               "pre-registered gates; comparing strategies with each other is itself a selection step, "
               "so count it as a trial before picking one to deploy.")
    if curves:
        cdf = pd.DataFrame(curves)
        cdf = cdf / cdf.bfill().iloc[0]
        for i, c in enumerate(cdf):
            COLORS[c] = STRATEGY_COLORS[i % len(STRATEGY_COLORS)]
        st.plotly_chart(line_chart(cdf, "Growth of ₹1, primary specs (each over its own development window)",
                                   log=True), width="stretch")
        if len(cdf.columns) > 1:
            corr = cdf.resample("ME").last().pct_change().corr()
            fig = px.imshow(corr, zmin=-1, zmax=1, color_continuous_scale=DIVERGING, text_auto=".2f",
                            title="Correlation of monthly returns")
            st.plotly_chart(fig, width="stretch")
    ledger = strategy_dir(strategies[0]).parent / "LEDGER.md"
    if ledger.exists():
        with st.expander("Strategy ledger (selection across strategies)", expanded=True):
            st.markdown(ledger.read_text())
    st.info("Add a strategy: create strategies/<id>/ with config.yaml and HYPOTHESIS.md, commit the "
            "pre-registration, then `uv run lab fetch` and `uv run lab research --strategy <id>`.")
    st.stop()

SID = choice
dev = load(SID, "dev")
hold = load(SID, "holdout")
cur = None
cur_dir = results_dir(SID) / "artifacts" / "current"
if (cur_dir / "ranking.parquet").exists():
    cur = (pd.read_parquet(cur_dir / "ranking.parquet"), json.loads((cur_dir / "meta.json").read_text()))

st.title(SID.replace("_", " ").title())
if dev is None:
    st.warning(f"No results yet. Run `uv run lab research --strategy {SID}` first.")
    st.stop()

s = dev["stats"]
MARKET = f"{s['benchmark_names']['market']} (buy & hold)"
REFERENCE = s["benchmark_names"].get("reference") or "Reference"
COLORS[MARKET], COLORS[REFERENCE] = MARKET_COLOR, REFERENCE_COLOR
with st.sidebar:
    st.header("View")
    period = st.radio("Period", ["Development", "Holdout"] if hold else ["Development"],
                      help="The holdout is shown only after holdout.py has been run (once).")
    after_tax = st.toggle("After tax", value=False,
                          help="After-tax NAV pays each financial year's tax in April by selling the book pro rata.")
    log_scale = st.toggle("Log scale", value=True)
    roll_win = st.slider("Rolling window (months)", 12, 60, 36, 6)
    st.divider()
    st.caption(f"Primary spec: **{s['primary']}**  \nDevelopment: {s['start']} → {s['end']}  \n"
               f"Trials in grid: {s['n_trials']} (+ walk-forward)")
    if hold:
        st.caption(f"Holdout: {hold['stats']['start']} → {hold['stats']['end']}")

data = dev if period == "Development" else hold
nav_all = data["nav"]
rf = nav_all["rf"]
frame = series_frame(nav_all, after_tax)

tabs = st.tabs(["Overview", "Performance", "Holdings & trading", "Costs & tax", "Signal",
                "Robustness & overfitting", "Next rebalance", "Data quality", "Assumptions"])

# ----- Overview -----------------------------------------------------------------------------
with tabs[0]:
    if s["killed"]:
        st.error("**Kill criterion triggered** — the pre-registered gates reject this strategy. "
                 "Results below are a negative finding, not a strategy to deploy.", icon="🛑")
    else:
        st.success("Kill criterion not triggered on the development period.", icon="✅")
    st.subheader("Pre-registered gates (development period)")
    gate_badges(s["gates"])
    if hold:
        hs = hold["stats"]
        st.markdown(f"**Holdout (used once, {hs['start']} → {hs['end']})**: "
                    f"{GOOD if hs['pass'] else BAD} — rule: {hs.get('pass_rule', 'after-tax CAGR above EW')}; "
                    f"after-tax CAGR vs EW {pct(hs['after_tax_active_cagr'], 2)}")
    else:
        st.info("Holdout not yet used. When every development gate has been reviewed, run "
                f"`uv run lab holdout --strategy {SID} --i-have-not-changed-anything` (one time only).",
                icon="🔒")

    st.subheader(f"Headline numbers — {period.lower()}, {'after tax' if after_tax else 'pre-tax, net of costs'}")
    nav = frame["Strategy"].dropna()
    ew = frame["EW"].dropna()
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1
    ew_cagr = (ew.iloc[-1] / ew.iloc[0]) ** (1 / yrs) - 1
    r = nav.pct_change().dropna()
    ex = r - rf.reindex(r.index).fillna(0)
    act = nav.resample("ME").last().pct_change() - ew.resample("ME").last().pct_change()
    k = st.columns(6)
    k[0].metric("CAGR", pct(cagr), delta=f"{(cagr - ew_cagr) * 100:+.1f} pp vs EW")
    k[1].metric("Sharpe (excess of cash)", num(ex.mean() / ex.std() * np.sqrt(252)))
    k[2].metric("Max drawdown", pct((nav / nav.cummax() - 1).min()))
    k[3].metric("Active IR vs EW", num(act.mean() / act.std() * np.sqrt(12)))
    if period == "Development":
        k[4].metric("Deflated Sharpe", num(s["dsr"]["dsr"], 3), help=f"Probability the Sharpe of the "
                    f"{s['selection_label']} series is real after {s['n_trials']} trials; needs > 0.95")
        k[5].metric("PBO", num(s["pbo"]["pbo"]), help="Probability of backtest overfitting (CSCV); needs < 0.50")
    else:
        k[4].metric("Months", len(act.dropna()))
        k[5].metric("Hit rate vs EW", pct((act.dropna() > 0).mean(), 0))
    st.plotly_chart(line_chart(frame, "Growth of ₹1", log=log_scale), width="stretch")
    if s.get("suspicious_signal"):
        st.warning("Mean IC above 0.15 — treat as a probable bug or lookahead.")

# ----- Performance ----------------------------------------------------------------------------
with tabs[1]:
    st.subheader("Risk and return")
    st.dataframe(perf_table(frame, rf), width="stretch")
    st.caption("Sharpe/Sortino on returns in excess of the strategy's cash asset (6% p.a. before it has history). "
               f"{REFERENCE} is a price series with no costs or tax, for reference only.")

    dd = frame / frame.cummax() - 1
    st.plotly_chart(line_chart(dd, "Drawdown", yfmt=".0%", height=300), width="stretch")

    m = frame.resample("ME").last().pct_change().dropna(how="all")
    w = roll_win
    roll = pd.DataFrame({c: m[c].rolling(w).mean() / m[c].rolling(w).std() * np.sqrt(12) for c in m})
    roll_act = (m["Strategy"] - m["EW"]).rolling(w)
    roll["Active IR vs EW"] = roll_act.mean() / roll_act.std() * np.sqrt(12)
    c1, c2 = st.columns(2)
    c1.plotly_chart(line_chart(roll.drop(columns="Active IR vs EW"), f"Rolling {w}-month Sharpe (raw)", height=320),
                    width="stretch")
    fig = line_chart(roll[["Active IR vs EW"]], f"Rolling {w}-month active IR vs EW", height=320)
    fig.update_traces(line_color=COLORS["Strategy"]); fig.add_hline(y=0, line_width=1, line_color="gray")
    c2.plotly_chart(fig, width="stretch")

    yr = frame.resample("YE").last()
    yr = pd.concat([frame.iloc[[0]], yr]).pct_change().dropna(how="all")
    yr.index = yr.index.year
    yl = yr.reset_index(names="Year").melt("Year", var_name="Series", value_name="Return")
    fig = px.bar(yl, x="Year", y="Return", color="Series", barmode="group", color_discrete_map=COLORS,
                 title="Calendar-year returns (first and last years partial)")
    fig.update_yaxes(tickformat=".0%"); fig.update_layout(height=380, legend_title=None)
    st.plotly_chart(fig, width="stretch")

    sm = m["Strategy"].dropna()
    hm = pd.DataFrame({"Year": sm.index.year, "Month": sm.index.strftime("%b"), "r": sm.values})
    hm = hm.pivot(index="Year", columns="Month", values="r")[
        [mm for mm in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] if mm in hm]]
    lim = float(np.nanpercentile(np.abs(hm.values), 95))
    fig = px.imshow(hm, color_continuous_scale=DIVERGING, zmin=-lim, zmax=lim, text_auto=".1%", aspect="auto",
                    title="Strategy monthly returns")
    fig.update_layout(height=40 + 26 * len(hm), coloraxis_colorbar_tickformat=".0%")
    st.plotly_chart(fig, width="stretch")

    c1, c2 = st.columns(2)
    am = (m["Strategy"] - m["EW"]).dropna()
    fig = px.histogram(am, nbins=40, title="Monthly active return vs EW", color_discrete_sequence=[COLORS["Strategy"]])
    fig.update_xaxes(tickformat=".0%"); fig.update_layout(showlegend=False, height=320)
    fig.add_vline(x=0, line_color="gray")
    c1.plotly_chart(fig, width="stretch")
    up = m["EW"] > 0
    cap = pd.DataFrame({"EW up months": [m.loc[up, "Strategy"].mean(), m.loc[up, "EW"].mean()],
                        "EW down months": [m.loc[~up, "Strategy"].mean(), m.loc[~up, "EW"].mean()]},
                       index=["Strategy", "EW"]).T.reset_index(names="Regime")
    fig = px.bar(cap.melt("Regime", var_name="Series", value_name="Avg monthly return"), x="Regime",
                 y="Avg monthly return", color="Series", barmode="group", color_discrete_map=COLORS,
                 title="Up/down capture vs EW")
    fig.update_yaxes(tickformat=".1%"); fig.update_layout(height=320, legend_title=None)
    c2.plotly_chart(fig, width="stretch")

# ----- Holdings & trading ---------------------------------------------------------------------
with tabs[2]:
    wts = data["weights"]
    wts = wts.loc[:, (wts > 0).any()]
    st.subheader("Holdings over time")
    order = wts.mean().sort_values(ascending=False).index
    wl = wts[order].reset_index(names="Date").melt("Date", var_name="Asset", value_name="Weight")
    fig = px.area(wl, x="Date", y="Weight", color="Asset", title="Target weights at each rebalance",
                  color_discrete_sequence=px.colors.qualitative.Safe)
    fig.update_yaxes(tickformat=".0%"); fig.update_layout(height=420, legend_title=None)
    fig.update_traces(line_width=0)
    st.plotly_chart(fig, width="stretch")
    st.caption("More than eight assets share this chart, so colours are not unique per asset; hover for the name.")

    c1, c2 = st.columns(2)
    freq = (wts > 0).mean().sort_values()
    fig = px.bar(freq, orientation="h", title="Share of months held", color_discrete_sequence=[COLORS["Strategy"]])
    fig.update_xaxes(tickformat=".0%"); fig.update_layout(showlegend=False, height=420, yaxis_title=None)
    c1.plotly_chart(fig, width="stretch")
    tr = data["trades"]
    tr = tr[tr["side"] != "sell_tax"].copy()
    tr["year"] = pd.to_datetime(tr["date"]).dt.year
    navs = nav_all["primary_net"]
    to = (tr.groupby("year")["value"].sum() / 2 / navs.groupby(navs.index.year).mean()).dropna()
    fig = px.bar(to, title="One-way turnover per year (× average NAV)", color_discrete_sequence=[COLORS["Strategy"]])
    fig.update_layout(showlegend=False, height=420, yaxis_title=None, xaxis_title=None)
    c2.plotly_chart(fig, width="stretch")

    last = wts.iloc[-1]
    st.markdown(f"**Last rebalance ({wts.index[-1].date()})**: " +
                ", ".join(f"{a} {v:.0%}" for a, v in last[last > 0].items()))
    with st.expander("All trades"):
        assets = st.multiselect("Asset", sorted(tr["asset"].unique()))
        view = tr[tr["asset"].isin(assets)] if assets else tr
        st.dataframe(view.drop(columns="year"), width="stretch", hide_index=True,
                     column_config={c: st.column_config.NumberColumn(format="₹%.0f")
                                    for c in ["value", "explicit", "spread", "impact", "total"]})

# ----- Costs & tax --------------------------------------------------------------------------------
with tabs[3]:
    tr_all = data["trades"].copy()
    tr_all["year"] = pd.to_datetime(tr_all["date"]).dt.year
    c1, c2 = st.columns(2)
    cy = tr_all.groupby("year")[["explicit", "spread", "impact"]].sum()
    fig = px.bar(cy, title="Trading cost by year (₹)", color_discrete_sequence=["#2a78d6", "#eb6834", "#1baf7a"])
    fig.update_layout(height=360, legend_title=None, yaxis_title=None, xaxis_title=None)
    c1.plotly_chart(fig, width="stretch")
    tot = cy.sum()
    c2.markdown("**Cost split over the period**")
    c2.dataframe(pd.DataFrame({"₹": tot.map(lambda v: f"{v:,.0f}"), "share": (tot / tot.sum()).map(pct)}),
                 width="stretch")
    if period == "Development":
        c2.metric("Safety margin (gross active ÷ cost drag)", f"{s['safety_margin']:.1f}×",
                  help="Transaction-costs skill: needs ≥ 2.5×")
        c2.metric("Capacity (95th-pct trade ≤ 5% of ETF ADV)", f"₹{s['capacity_inr'] / 1e7:,.1f} crore")

    if period == "Development":
        cs = dev["cost_sensitivity"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=cs["cost_x"], y=cs["CAGR"], name="Strategy CAGR", mode="lines+markers",
                                 line=dict(color=COLORS["Strategy"], width=2), marker=dict(size=9)))
        fig.add_trace(go.Scatter(x=cs["cost_x"], y=cs["EW_CAGR"], name="EW CAGR", mode="lines+markers",
                                 line=dict(color=COLORS["EW"], width=2), marker=dict(size=9)))
        fig.update_layout(title="Cost sensitivity (0× = frictionless, 3× = stress)", height=340,
                          hovermode="x unified", xaxis_title="cost multiplier")
        fig.update_yaxes(tickformat=".1%")
        st.plotly_chart(fig, width="stretch")

    st.subheader("Tax")
    if period == "Development" and "tax_paid" in dev:
        tp = pd.DataFrame({"Strategy": dev["tax_paid"]["tax"], "EW": dev["ew_tax_paid"]["tax"]})
        tp.index = [f"FY{d.year - 1}-{str(d.year)[2:]}" for d in pd.to_datetime(tp.index)]
        fig = px.bar(tp.fillna(0), barmode="group", color_discrete_map=COLORS, title="Tax paid per financial year (₹)")
        fig.update_layout(height=360, legend_title=None, yaxis_title=None)
        st.plotly_chart(fig, width="stretch")
        p = s["perf"]
        st.markdown(
            f"- After-tax CAGR: strategy **{pct(p['primary_after_tax'].get('CAGR_after_tax'))}**, "
            f"EW {pct(p['ew_after_tax'].get('CAGR_after_tax'))}, {MARKET} "
            f"{pct(p['market_after_tax'].get('CAGR_after_tax'))}\n"
            f"- Tax drag on the strategy: {pct(p['primary_net']['CAGR'] - p['primary_after_tax']['CAGR_after_tax'], 2)} "
            f"a year (pre-tax net CAGR minus after-tax CAGR)\n"
            f"- Tax still owed if the book were sold on the last day: ₹{s['liquidation_tax']:,.0f} "
            f"(EW ₹{s['ew_liquidation_tax']:,.0f})")
    st.caption("Rules after 23-Jul-2024 applied to all history: equity 20% ≤12m, 12.5% >12m above ₹1.25 lakh; "
               "gold/silver at slab ≤12m, 12.5% >12m; debt/liquid at slab; 4% cess; set-off and carry-forward modelled.")

# ----- Signal ---------------------------------------------------------------------------------------
with tabs[4]:
    sig = dev["signal_table"]
    xs = s["signal_test"] == "cross_sectional"
    st.subheader(("Cross-sectional rank IC" if xs else "Time-series test: on-minus-off spread") + " (development period)")
    st.dataframe(sig.style.format({c: "{:.4f}" for c in sig.columns if c not in ("signal", "n")}),
                 width="stretch", hide_index=True)
    st.caption("Gate: primary signal Newey-West t > 2 and the same sign in both halves. " + (
        "Typical cross-sectional ICs are 0.02–0.05; above 0.10 suspect lookahead." if xs else
        "Spread = mean forward excess-over-cash return of assets whose trend is on minus those whose trend is off, "
        "per decision. on_excess_m / off_excess_m are the two sides; IC_xs is the cross-sectional rank IC."))
    ser = dev["signal_series"]["value"]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=ser.index, y=ser, name=s["signal_label"], marker_color="#86b6ef"))
    fig.add_trace(go.Scatter(x=ser.index, y=ser.rolling(roll_win, min_periods=6).mean(), name=f"{roll_win}-decision mean",
                             line=dict(color=COLORS["Strategy"], width=2)))
    fig.update_layout(title=f"Primary signal: {s['signal_label']}", height=360, hovermode="x unified",
                      legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom"))
    if not xs:
        fig.update_yaxes(tickformat=".1%")
    st.plotly_chart(fig, width="stretch")
    c1, c2 = st.columns(2)
    dcols = [c for c in sig.columns if c.startswith(("IC_", "spread_")) and c[-1] == "m" and c != "IC_xs"]
    dl = sig.set_index("signal")[dcols].reset_index().melt("signal", var_name="Horizon", value_name="Mean")
    fig = px.line(dl, x="Horizon", y="Mean", color="signal", markers=True, title="Decay by forward horizon")
    fig.update_layout(height=360, legend_title=None)
    c1.plotly_chart(fig, width="stretch")
    by_year = ser.groupby(ser.index.year).mean()
    fig = px.bar(by_year, title="Mean by year", color=by_year.values > 0,
                 color_discrete_map={True: "#2a78d6", False: "#d03b3b"})
    fig.update_layout(showlegend=False, height=360, yaxis_title=None, xaxis_title=None)
    c2.plotly_chart(fig, width="stretch")

# ----- Robustness ------------------------------------------------------------------------------------
with tabs[5]:
    st.subheader(f"Selection-adjusted statistics ({s['selection_label']}, monthly)")
    d, pb = s["dsr"], s["pbo"]
    k = st.columns(6)
    k[0].metric("Deflated Sharpe (primary)", num(d["dsr"], 3), help="> 0.95 required")
    k[1].metric("PBO", num(pb["pbo"]), help="< 0.50 required")
    k[2].metric("Haircut, best Sharpe", f"{s['haircut']:+.2f}", help="Best annualised Sharpe in the grid minus the "
                "expected maximum under the null; negative means no evidence of skill")
    k[3].metric("Bootstrap p (mean > 0)", num(s["bootstrap_p"], 3))
    k[4].metric("Sharpe > EW, bootstrap p", num(s.get("sharpe_diff_p"), 3),
                help="Paired block bootstrap on excess-over-cash returns; one-sided")
    k[5].metric("Robustness", pct(s["robustness"], 0),
                help=("Share of the grid with positive active IR" if s["gate_type"] == "relative_return"
                      else "Share of the grid passing the strategy-gate rule") + "; target > 60%")
    st.caption(f"Per-month Sharpe {d['sr']:.3f} vs expected maximum under the null {d['e_max_sr']:.3f} "
               f"across {s['n_trials']} trials; skew {d['skew']:.2f}, kurtosis {d['kurtosis']:.2f}, n = {d['n']}. "
               f"PBO from {pb['n_splits']} CSCV splits; in-sample-best per-month SR {pb['is_sr_best_mean']:.3f} → "
               f"out-of-sample {pb['oos_sr_best_mean']:.3f}.")

    trials = dev["trials"].copy()
    fields = s["spec_fields"]
    facet, colf = fields[0], fields[-1]
    mids = fields[1:-1] or [fields[0]]
    trials["_row"] = trials[mids].astype(str).agg("/".join, axis=1)
    metric = st.selectbox("Heatmap metric", ["sel_SR", "active_IR", "Sharpe_net", "MaxDD", "Calmar",
                                             "CAGR_net", "CAGR_after_tax", "active_ann"])
    groups = list(trials.groupby(facet))
    cols = st.columns(len(groups))
    vals = trials[metric]
    lim = float(np.nanmax(np.abs(vals))) or 1
    for col, (fv, sub) in zip(cols, groups):
        hm = sub.pivot(index="_row", columns=colf, values=metric)
        fig = px.imshow(hm, color_continuous_scale=DIVERGING, zmin=-lim, zmax=lim,
                        text_auto=".2f" if metric in ("sel_SR", "active_IR", "Sharpe_net", "Calmar") else ".1%",
                        title=f"{facet} = {fv}", aspect="auto", labels=dict(y="/".join(mids), x=colf))
        fig.update_layout(height=330, coloraxis_showscale=False)
        col.plotly_chart(fig, width="stretch")
    st.caption("Every cell is a trial. A primary spec surrounded by much weaker neighbours (a cliff) "
               "or sitting on the edge of the grid is a sign of overfitting.")

    tn = dev["trial_nav"]
    tn = tn / tn.bfill().iloc[0]
    fig = go.Figure()
    for c in tn:
        if c != s["primary"]:
            fig.add_trace(go.Scatter(x=tn.index, y=tn[c], mode="lines", line=dict(width=1, color="#c3c2b7"),
                                     name=c, showlegend=False, hovertemplate=f"{c}: %{{y:.2f}}<extra></extra>"))
    ewn = dev["nav"]["ew_net"]
    fig.add_trace(go.Scatter(x=ewn.index, y=ewn / ewn.iloc[0], mode="lines", name="EW",
                             line=dict(width=2, color=COLORS["EW"])))
    fig.add_trace(go.Scatter(x=tn.index, y=tn[s["primary"]], mode="lines", name=f"Primary ({s['primary']})",
                             line=dict(width=2.5, color=COLORS["Strategy"])))
    fig.update_layout(title=f"All {s['n_trials']} trials (grey) vs primary and EW, net of costs", height=420)
    fig.update_yaxes(type="log" if log_scale else "linear")
    st.plotly_chart(fig, width="stretch")

    show = trials.drop(columns=fields + ["_row"]).sort_values("sel_SR", ascending=False)
    st.dataframe(show.style.format({"CAGR_net": "{:.1%}", "MaxDD": "{:.1%}", "MaxDD_after_tax": "{:.1%}",
                                    "CAGR_after_tax": "{:.1%}", "active_ann": "{:.2%}", "Sharpe_net": "{:.2f}",
                                    "Calmar": "{:.2f}", "active_IR": "{:.2f}", "sel_SR": "{:.2f}",
                                    "turnover_pa": "{:.2f}"}),
                 width="stretch", hide_index=True)

    st.subheader(f"Walk-forward selection (trial {s['n_trials'] + 1})")
    wf = s["walk_forward"]
    k = st.columns(3)
    k[0].metric("Expanding: mean p.a. / Sharpe", f"{pct(wf['expanding']['ann'], 2)} / {num(wf['expanding']['SR'])}")
    k[1].metric("Rolling 5y: mean p.a. / Sharpe", f"{pct(wf['rolling']['ann'], 2)} / {num(wf['rolling']['SR'])}")
    k[2].metric("Primary, same window", f"{pct(s['primary_same_window']['ann'], 2)} / "
                                        f"{num(s['primary_same_window']['SR'])}")
    wfs = dev["walk_forward"].copy()
    am = dev["selection_monthly"][s["primary"]].reindex(wfs.index)
    cum = pd.DataFrame({"Walk-forward (expanding)": (1 + wfs["expanding"]).cumprod(),
                        "Walk-forward (rolling)": (1 + wfs["rolling"]).cumprod(), "Primary": (1 + am).cumprod()})
    fig = line_chart(cum, f"Cumulative {s['selection_label']} return", height=340)
    fig.update_traces(selector=dict(name="Primary"), line_color=COLORS["Strategy"])
    fig.update_traces(selector=dict(name="Walk-forward (expanding)"), line_color=COLORS["Walk-forward"])
    fig.update_traces(selector=dict(name="Walk-forward (rolling)"), line_color="#6250d6")
    st.plotly_chart(fig, width="stretch")
    st.caption("Picks by year (expanding): " + ", ".join(f"{y}: {p}" for y, p in wf["expanding"]["picks"]))

# ----- Next rebalance -----------------------------------------------------------------------------
with tabs[6]:
    if cur is None:
        st.info(f"Run `uv run lab current --strategy {SID}` to score the universe on the latest close.")
    else:
        rk, meta = cur
        st.subheader(f"Ranking on {meta['asof']} — {meta['spec']}")
        st.caption("Operational view: what the strategy would hold if the month ended on this close. "
                   "The real decision is taken on the last NSE session of the month and traded the next session.")
        hold_now = rk[rk["target_weight"] > 0]
        st.markdown("**Would hold:** " + ", ".join(f"{r.etf} ({r.asset}) {r.target_weight:.0%}" for r in hold_now.itertuples()))
        fig = px.bar(rk.dropna(subset=["score"]).sort_values("score"), x="score", y="asset", orientation="h",
                     color=rk.dropna(subset=["score"]).sort_values("score")["target_weight"] > 0,
                     color_discrete_map={True: COLORS["Strategy"], False: "#c3c2b7"}, title="Signal score")
        fig.update_layout(showlegend=False, height=520, yaxis_title=None)
        fig.update_xaxes(tickformat=".0%" if meta["spec"].startswith("ret") else ".2f")
        st.plotly_chart(fig, width="stretch")
        st.dataframe(rk, width="stretch", hide_index=True,
                     column_config={"score": st.column_config.NumberColumn(format="%.3f"),
                                    "target_weight": st.column_config.NumberColumn(format="%.2f"),
                                    "vol_63d_ann": st.column_config.NumberColumn(format="%.3f"),
                                    "ret_1m": st.column_config.NumberColumn(format="%.3f"),
                                    "adv_cr": st.column_config.NumberColumn("ADV (₹ cr)", format="%.1f")})

# ----- Data quality --------------------------------------------------------------------------------
with tabs[7]:
    f = results_dir(SID) / "data_validation.md"
    st.markdown(f.read_text() if f.exists() else "Not generated yet.")

# ----- Assumptions ---------------------------------------------------------------------------------
with tabs[8]:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Pre-registration")
        st.markdown((strategy_dir(SID) / "HYPOTHESIS.md").read_text())
    with c2:
        st.subheader(f"strategies/{SID}/config.yaml")
        st.code((strategy_dir(SID) / "config.yaml").read_text(), language="yaml")
        t = results_dir(SID) / "TRIALS.md"
        if t.exists():
            st.markdown(t.read_text())
