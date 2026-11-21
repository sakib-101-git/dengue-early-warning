"""Dengue Watch: ward-level dengue early-warning dashboard.

Run from the repo root:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import bisect
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `src` and `app` importable

from app import charts, components as ui  # noqa: E402
from app.theme import BRAND, BRAND_SOFT, CSS, HEAT  # noqa: E402
from src.config import HORIZONS, METRICS_JSON, TEST_YEARS  # noqa: E402
from src.predict import RiskEngine  # noqa: E402

st.set_page_config(page_title="Dengue Watch", page_icon=":material/water_drop:", layout="wide",
                   initial_sidebar_state="collapsed")
st.html(CSS)


# ---------------------------------------------------------------- cached resources
@st.cache_resource(show_spinner="Loading models")
def engine() -> RiskEngine:
    return RiskEngine()


@st.cache_data
def table(h: int) -> pd.DataFrame:
    return engine().forecast_table(h)


@st.cache_data
def metrics() -> dict:
    return json.loads(Path(METRICS_JSON).read_text())


@st.cache_data
def test_baseline_mae() -> float:
    """'Same week last year' error on the held-out test years, for the headline comparison."""
    r = engine().raw.sort_values(["ward_id", "date"])
    last = r.groupby("ward_id").dengue_cases.shift(52)
    m = r.date.dt.year.isin(TEST_YEARS) & last.notna()
    return float((r.loc[m, "dengue_cases"] - last[m]).abs().mean())


E = engine()
RAW = E.raw
CITIES = sorted(RAW.city.unique())

# Keep shared selections alive when a page doesn't render that widget
for k in ("city", "ward", "h", "week", "rain", "temp", "hum"):
    if k in st.session_state:
        st.session_state[k] = st.session_state[k]
st.session_state.setdefault("city", "Dhaka North")
st.session_state.setdefault("h", 3)
st.session_state.setdefault("week", pd.Timestamp("2025-07-07").date())
for k in ("rain", "temp", "hum"):
    st.session_state.setdefault(k, 0)

# ---------------------------------------------------------------- top bar + navigation
with st.container(key="topbar", horizontal=True, vertical_alignment="center", horizontal_alignment="distribute"):
    st.markdown(ui.BRAND, unsafe_allow_html=True, width="content")
    page = st.segmented_control("Page", ["Ward", "City", "Model"], default="Ward", key="page", required=True,
                                label_visibility="collapsed")
page = page or st.session_state.get("_last_page", "Ward")
if st.session_state.get("_last_page") != page:  # animate only when the page changes
    st.html("<style>.st-key-view{animation:dwRise .4s cubic-bezier(.2,.8,.2,1) both}</style>")
st.session_state["_last_page"] = page


def snap_week(h: int) -> pd.Timestamp:
    """Map the chosen date to the latest week that has data (and full lag history)."""
    weeks = sorted(table(h).issue_date.unique())
    chosen = pd.Timestamp(st.session_state.week)
    i = max(bisect.bisect_right(weeks, chosen) - 1, 0)
    return pd.Timestamp(weeks[i])


def week_input(col) -> None:
    weeks = table(st.session_state.h).issue_date
    lo, hi = weeks.min().date(), weeks.max().date()
    st.session_state.week = min(max(st.session_state.week, lo), hi)  # clamp stale/out-of-range state
    col.date_input("Data up to", key="week", min_value=weeks.min().date(), max_value=weeks.max().date(),
                   format="DD/MM/YYYY", help="Forecasts use weather up to this week only.")


def horizon_input(col) -> None:
    col.segmented_control("Weeks ahead", HORIZONS, key="h", format_func=lambda x: f"{x} weeks", required=True,
                          width="stretch", help="How far ahead to warn. Rain needs 2–4 weeks to turn into cases.")


def md(html: str) -> None:
    st.markdown(html, unsafe_allow_html=True)


# ================================================================ WARD
def page_ward() -> None:
    title = st.container()
    with st.container(key="controls"):
        c1, c2, c3, c4, c5 = st.columns([1.1, 1, 1.3, 1.05, 1.05], vertical_alignment="bottom")
        c1.selectbox("City", CITIES, key="city")
        wards = sorted(RAW.loc[RAW.city == st.session_state.city, "ward_id"].unique())
        if st.session_state.get("ward") not in wards:
            st.session_state.ward = wards[0]
        c2.selectbox("Ward", wards, key="ward", format_func=ui.short_ward)
        horizon_input(c3)
        week_input(c4)
        with c5.popover("Simulate weather", icon=":material/tune:", width="stretch", key="sim"):
            md(ui.head("Simulate weather", "Change the last 4 weeks of weather and see the forecast move."))
            st.slider("Rain in the last 4 weeks", -80, 150, key="rain", step=10, format="%d%%")
            st.slider("Temperature", -4, 4, key="temp", step=1, format="%+d °C")
            st.slider("Humidity", -20, 20, key="hum", step=2, format="%+d pts")
            st.button("Reset", icon=":material/restart_alt:",
                      on_click=lambda: st.session_state.update(rain=0, temp=0, hum=0))

    ward, h, issue = st.session_state.ward, st.session_state.h, snap_week(st.session_state.h)
    sim = any(st.session_state[k] for k in ("rain", "temp", "hum"))
    frame = E.what_if_frame(ward, issue, h, st.session_state.rain, st.session_state.temp,
                            st.session_state.hum) if sim else None
    real = E.forecast(ward, issue, h)
    fc = E.forecast(ward, issue, h, frame=frame) if sim else real

    weeks = RAW[(RAW.ward_id == ward) & (RAW.date <= issue)].tail(12)[["date", "rainfall_mm"]].copy()
    if sim:
        weeks["rainfall_mm"] = frame.set_index("date").loc[weeks.date, "rain_lag_0w"].values
        st.html(f"<style>.st-key-sim [data-testid='stPopoverButton']{{border-color:{BRAND} !important;"
                f"background:{BRAND_SOFT} !important;color:{BRAND} !important}}"
                f".st-key-sim [data-testid='stIconMaterial']{{color:{BRAND} !important}}</style>")

    with title:
        md(ui.page_title(ui.short_ward(ward), f"{fc.city} · forecast for the week of {ui.day_mon(fc.target_date, True)}"))

    left, right = st.columns([1, 2.05])
    with left, st.container(border=True, key="panel_summary", height="stretch"):
        sim_note = ""
        if sim:
            sim_note = f"Simulated weather: {fc.expected_cases - real.expected_cases:+.1f} cases vs. actual weather"
        recorded = "Not yet known" if real.actual is None else f"{real.actual:.0f} cases"
        md(ui.summary(fc, ward, h, issue, recorded, sim_note))
    with right, st.container(border=True, key="panel_rain", height="stretch"):
        md(ui.head("Rain and risk", f"Weekly rain, and which weeks feed the {ui.day_mon(fc.target_date)} forecast",
                   tip="Rain needs 2–4 weeks to turn into dengue cases: mosquitoes breed in standing water. "
                       "Hover a bar for details."))
        st.plotly_chart(charts.rain_risk(weeks, issue, fc.target_date, fc.tier), config=charts.PLOTLY_CONFIG,
                        width="stretch")
        md(ui.rain_legend(fc.tier, h > 1))

    hist = table(h)
    hist = hist[(hist.ward_id == ward) & (hist.target_date.dt.year == fc.target_date.year)]
    left, right = st.columns([1, 1.45])
    with left, st.container(border=True, key="panel_drivers", height="stretch"):
        base, grouped, detail = E.explain(ward, issue, h, frame=frame)
        md(ui.head("What pushes the risk", "Effect on expected cases",
                   tip=f"Change against a typical ward-week ({base:.1f} cases)."))
        md(ui.driver_rows(grouped))
    with right, st.container(border=True, key="panel_season", height="stretch"):
        md(ui.head(f"{fc.target_date.year} season", "Forecast vs. recorded cases, per week"))
        st.plotly_chart(charts.season(hist, fc.target_date), config=charts.PLOTLY_CONFIG, width="stretch")
        md(ui.season_legend())


# ================================================================ CITY
def page_city() -> None:
    title = st.container()
    with st.container(key="controls"):
        c1, c2, c3, _ = st.columns([1.1, 1.3, 1.05, 2.2], vertical_alignment="bottom")
        c1.selectbox("City", CITIES, key="city")
        horizon_input(c2)
        week_input(c3)
    city, h, issue = st.session_state.city, st.session_state.h, snap_week(st.session_state.h)
    target = issue + pd.Timedelta(weeks=h)
    tab = table(h)
    now = tab[(tab.city == city) & (tab.issue_date == issue)].sort_values("expected_cases", ascending=False)

    with title:
        md(ui.page_title(city, f"Week of {ui.day_mon(target, True)} · {h} weeks ahead, data up to {ui.day_mon(issue)}"))

    hot = now[now.tier.isin(["High", "Critical"])]
    with st.container(border=True, key="panel_need"):
        md(ui.need_action(len(hot), len(now), target) + ui.tier_mix(now.tier.value_counts()))
    with st.container(border=True, key="panel_rank"):
        md(ui.head("Where to send teams first", "Expected cases and likely range, highest first",
                   tip="High and Critical wards need action. The likely range covers 8 in 10 weeks."))
        md(ui.ranking(now))

    with st.container(border=True, key="panel_heat"):
        years = sorted(tab.target_date.dt.year.unique())[::-1]
        hl, hr = st.columns([3, 1], vertical_alignment="top")
        hl.markdown(ui.head("Risk through the year", "Forecast risk for each ward and week"), unsafe_allow_html=True)
        with hr, st.container(key="yearpick"):
            year = st.selectbox("Year", years, index=years.index(target.year), label_visibility="collapsed")
        hm = tab[(tab.city == city) & (tab.target_date.dt.year == year)]
        st.plotly_chart(charts.heatmap(hm, target), config=charts.PLOTLY_CONFIG, width="stretch")
        md(ui.tier_legend(HEAT))


# ================================================================ MODEL
def page_model() -> None:
    title = st.container()
    with st.container(key="controls"):
        c1, _ = st.columns([1.3, 4.4], vertical_alignment="bottom")
        horizon_input(c1)
    h = st.session_state.h
    m = metrics()
    hm = m["horizons"][str(h)]
    test = hm["test"]
    gain = test_baseline_mae() / test["MAE"]

    with title:
        md(ui.page_title("Model", "Tested on unseen 2024–2025 · trained 2017–2022 · tuned 2023"))

    md(ui.stats([
        (f"{test['MAE']:.2f}", "cases off per ward-week, on average"),
        (f"{test['high_risk_recall']:.0%}", "of high-risk weeks flagged in advance"),
        (f"{hm['test_interval_coverage_80']:.0%}", "of weeks inside the likely range"),
        (f"{gain:.1f}×", "more accurate than “same week last year”"),
    ], highlight=3))

    left, right = st.columns(2)
    with left, st.container(border=True, key="panel_models", height="stretch"):
        md(ui.head("Average error by model", "2023 season, cases off per ward-week. Lower is better."))
        st.plotly_chart(charts.model_errors(pd.DataFrame(hm["validation_comparison"]).T),
                        config=charts.PLOTLY_CONFIG, width="stretch")
    with right, st.container(border=True, key="panel_years", height="stretch"):
        md(ui.head("Average error each year", f"{h} weeks ahead highlighted. Lower is better."))
        st.plotly_chart(charts.backtest(m, HORIZONS, selected=h), config=charts.PLOTLY_CONFIG, width="stretch")

    with st.container(border=True, key="panel_steps"):
        md(ui.head("How a forecast is made") + ui.steps([
            ("Weather in", "Weekly rain, temperature and humidity for each ward."),
            ("Look back", "Rain from 0–8 weeks ago and rolling totals, since rain takes 2–4 weeks to become cases."),
            ("Predict", "A LightGBM model, one per horizon, estimates next cases from weather, ward and season."),
            ("Rank", "Expected cases become Low, Medium, High or Critical using training-data cut-offs."),
        ]))

    with st.expander("All numbers"):
        t = hm["tier_thresholds_cases"]
        st.markdown(f"Risk cut-offs (cases per ward-week): Low < {t[0]:g}, Medium < {t[1]:g}, "
                    f"High < {t[2]:g}, Critical ≥ {t[2]:g}")
        st.dataframe(pd.DataFrame(hm["validation_comparison"]).T, width="stretch")
        st.dataframe(pd.DataFrame(hm["backtest_by_year"]).set_index("year"), width="stretch")


with st.container(key="view"):
    {"Ward": page_ward, "City": page_city, "Model": page_model}[page]()
