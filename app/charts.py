"""Plotly figures styled to the dashboard tokens."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from app.components import short_ward
from app.theme import BRAND, FONT, HEAT, INK, INK_2, INK_3, LINE, LINE_2, RAIN_SOFT, TIER

PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}
GRID = "#F0F0F2"
MUTED_BAR = "#D9D9DE"
MUTED_LINE = "#BDBDC4"


def _base(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=4, r=12, t=8, b=4),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=INK_3, size=12),
        hoverlabel=dict(bgcolor="white", bordercolor=LINE_2, align="left",
                        font=dict(family=FONT, color=INK, size=12.5)),
        showlegend=False, hovermode="closest", dragmode=False,
        transition=dict(duration=350, easing="cubic-in-out"),
    )
    fig.update_xaxes(showgrid=False, linecolor=LINE, ticks="", zeroline=False, fixedrange=True,
                     tickfont=dict(color=INK_3, size=11.5))
    fig.update_yaxes(gridcolor=GRID, gridwidth=1, zeroline=False, ticks="", showline=False, fixedrange=True,
                     tickfont=dict(color=INK_3, size=11.5), ticklabelstandoff=6)
    return fig


def season(hist: pd.DataFrame, target_date) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist.target_date, y=hist.high_80, line=dict(width=0, shape="spline"), hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=hist.target_date, y=hist.low_80, fill="tonexty", line=dict(width=0, shape="spline"),
                             fillcolor="rgba(11,106,128,0.12)", hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=hist.target_date, y=hist.expected_cases, line=dict(color=BRAND, width=2.25, shape="spline", smoothing=0.6),
        customdata=np.c_[hist.low_80, hist.high_80], name="Forecast",
        hovertemplate="<b>Week of %{x|%d %b}</b><br>Forecast %{y:.1f} cases"
                      "<br>Likely %{customdata[0]:.0f}–%{customdata[1]:.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=hist.target_date, y=hist.actual, mode="markers", name="Recorded",
                             marker=dict(color=INK, size=5.5, line=dict(color="white", width=1)),
                             hovertemplate="<b>Week of %{x|%d %b}</b><br>Recorded %{y:.0f} cases<extra></extra>"))
    fig.add_vline(x=pd.Timestamp(target_date), line=dict(color=INK_3, width=1, dash="dot"))
    fig.update_yaxes(title=None, rangemode="tozero", nticks=5)
    fig.update_xaxes(tickformat="%b", tickangle=0, showline=False)
    return _base(fig, 248)


def rain_risk(weeks: pd.DataFrame, issue_date, target_date, tier: str) -> go.Figure:
    """Weekly rain (mm) up to the issue week, the 2–4-week breeding window shaded, the unknown weeks
    greyed, and the forecast week flagged in its risk colour. One axis: millimetres of rain."""
    issue, target = pd.Timestamp(issue_date), pd.Timestamp(target_date)
    wk = pd.Timedelta(weeks=1)
    d = weeks.assign(lag=((target - weeks.date).dt.days // 7).astype(int))
    hot = d.lag.between(2, 4).to_numpy()
    top = max(float(d.rainfall_mm.max()), 40.0) * 1.32
    t = TIER[tier]

    fig = go.Figure()
    # unknown weeks between the data cut-off and the forecast week
    if target - issue > wk:
        fig.add_vrect(x0=issue + wk / 2, x1=target - wk / 2, fillcolor="rgba(24,24,27,0.035)", line_width=0, layer="below")
    # breeding window: rain 2–4 weeks before the forecast week
    fig.add_vrect(x0=target - 4.5 * wk, x1=target - 1.5 * wk, fillcolor="rgba(11,106,128,0.07)", line_width=0,
                  layer="below")
    fig.add_annotation(x=target - 3 * wk, y=top, yanchor="top", yshift=-2, showarrow=False, text="Breeding window",
                       font=dict(size=11.5, color=BRAND, weight=600))
    # forecast week column
    fig.add_vrect(x0=target - 0.42 * wk, x1=target + 0.42 * wk, fillcolor=t["soft"], line_width=0, layer="below")
    fig.add_shape(type="line", x0=target, x1=target, y0=0, y1=top * 0.74, line=dict(color=t["bg"], width=2))
    fig.add_trace(go.Scatter(x=[target], y=[top * 0.74], mode="markers", hoverinfo="skip",
                             marker=dict(size=11, color=t["bg"], line=dict(color="white", width=2.5))))
    fig.add_annotation(x=target, y=top * 0.74, yshift=14, showarrow=False, yanchor="bottom", text=f"{tier} risk",
                       font=dict(size=11.5, color=t["text"], weight=600))
    # data cut-off
    fig.add_shape(type="line", x0=issue + wk / 2, x1=issue + wk / 2, y0=0, y1=top, line=dict(color=LINE_2, width=1))

    fig.add_trace(go.Bar(
        x=d.date, y=d.rainfall_mm, width=4.2 * 24 * 3600 * 1000,
        marker=dict(color=np.where(hot, BRAND, RAIN_SOFT), cornerradius=4, line=dict(width=0)),
        text=[f"{v:.0f}" if h_ else "" for v, h_ in zip(d.rainfall_mm, hot)], textposition="outside",
        textfont=dict(size=11.5, color=INK), cliponaxis=False,
        customdata=np.where(hot, "In the breeding window", "Outside the breeding window"),
        hovertemplate="<b>Week of %{x|%-d %b}</b><br>%{y:.0f} mm of rain<br>%{customdata}<extra></extra>"))

    ticks = list(d.date.iloc[::-3][::-1]) + [target]
    fig.update_xaxes(range=[d.date.min() - 0.6 * wk, target + 0.6 * wk], tickvals=ticks, showline=False,
                     ticktext=[f"{x.day} {x:%b}" if x != target else f"<b>{x.day} {x:%b}</b>" for x in ticks])
    fig.update_yaxes(range=[0, top], nticks=4, ticksuffix=" mm", showline=False)
    return _base(fig, 236).update_layout(bargap=0, margin=dict(l=4, r=8, t=4, b=4))


def heatmap(hm: pd.DataFrame, marker=None) -> go.Figure:
    order = ["Low", "Medium", "High", "Critical"]
    tier = hm.pivot(index="ward_id", columns="target_date", values="tier").sort_index(ascending=False)
    val = hm.pivot(index="ward_id", columns="target_date", values="expected_cases").reindex(tier.index)
    z = tier.replace({t: i for i, t in enumerate(order)}).astype(float)
    scale = []
    for i, t in enumerate(order):
        scale += [[i / 4, HEAT[t]], [(i + 1) / 4, HEAT[t]]]
    fig = go.Figure(go.Heatmap(
        z=z.values, x=z.columns, y=[short_ward(w) for w in z.index], zmin=-0.5, zmax=3.5,
        colorscale=scale, showscale=False, xgap=2, ygap=2,
        customdata=np.dstack([tier.values, val.round(1).values]),
        hovertemplate="<b>%{y}</b>, week of %{x|%d %b}<br>%{customdata[0]} risk, %{customdata[1]} cases<extra></extra>"))
    if marker is not None and len(z.columns) and z.columns.min() <= marker <= z.columns.max():
        fig.add_vline(x=marker, line=dict(color=INK, width=1.5))
    fig.update_xaxes(tickformat="%b", tickangle=0, showline=False)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11, color=INK_2))
    return _base(fig, 40 + 22 * len(z))


def model_errors(comp: pd.DataFrame) -> go.Figure:
    labels = {
        "Baseline: persistence (cases now)": "Copy this week",
        "Baseline: same week last year": "Same week last year",
        "Poisson GLM (statistical)": "Poisson regression",
        "Random Forest": "Random forest",
        "XGBoost (Poisson)": "XGBoost",
        "LightGBM (Poisson)  <- deployed": "LightGBM (ours)",
    }
    d = comp.assign(label=[labels.get(i, i) for i in comp.index]).sort_values("MAE", ascending=False)
    ours = d.label.str.contains("ours").to_numpy()
    fig = go.Figure(go.Bar(
        x=d.MAE, y=d.label, orientation="h", width=0.62,
        marker=dict(color=np.where(ours, BRAND, MUTED_BAR), cornerradius=5, line=dict(width=0)),
        text=[f"  {v:.2f}" for v in d.MAE], textposition="outside", cliponaxis=False,
        textfont=dict(color=[BRAND if o else INK_2 for o in ours], size=12),
        hovertemplate="%{y}: %{x:.2f} cases off per ward-week<extra></extra>"))
    fig.update_xaxes(showticklabels=False, showgrid=False, showline=False, range=[0, d.MAE.max() * 1.16])
    fig.update_yaxes(showgrid=False, tickfont=dict(color=INK_2, size=12.5), ticklabelstandoff=10)
    return _base(fig, 252)


def backtest(metrics: dict, horizons, selected: int | None = None) -> go.Figure:
    """Error by year per horizon; the selected horizon is drawn in the brand colour, others muted."""
    fig = go.Figure()
    order = sorted(horizons, key=lambda hz: hz == selected)  # draw the selected line last (on top)
    frames = {hz: pd.DataFrame(metrics["horizons"][str(hz)]["backtest_by_year"]) for hz in horizons}
    label_y, gap = {}, 0.09 * max(f.MAE.max() for f in frames.values())  # nudge end labels apart
    for hz in sorted(horizons, key=lambda k: frames[k].MAE.iloc[-1]):
        y = frames[hz].MAE.iloc[-1]
        label_y[hz] = max([y] + [v + gap for v in label_y.values()])
    for hz in order:
        bt = frames[hz]
        on = hz == selected
        color = BRAND if on else MUTED_LINE
        fig.add_trace(go.Scatter(
            x=bt.year, y=bt.MAE, mode="lines+markers", name=f"{hz} weeks ahead",
            line=dict(color=color, width=2.25 if on else 1.5),
            marker=dict(size=6.5 if on else 5, color=color, line=dict(color="white", width=1.2)),
            hovertemplate=f"<b>{hz} weeks ahead</b>, %{{x}}<br>%{{y:.2f}} cases off<extra></extra>"))
        fig.add_annotation(x=bt.year.iloc[-1], y=label_y[hz], text=f"{hz} wk", showarrow=False,
                           xanchor="left", xshift=9, font=dict(color=BRAND if on else INK_3, size=11.5,
                                                               weight=600 if on else 400))
    fig.update_xaxes(dtick=1, range=[2018.6, 2025.55], showline=False)
    fig.update_yaxes(rangemode="tozero", nticks=5)
    return _base(fig, 252).update_layout(margin=dict(r=40))
