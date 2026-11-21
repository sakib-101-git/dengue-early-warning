"""Multi-week, ward-level lag feature pipeline.

Biology behind the lags (from the brief): rain -> stagnant water -> 7-14 days for Aedes eggs
to become adults -> viral incubation -> symptoms. EDA on this dataset confirms it: the
correlation between weekly cases and rainfall peaks at a 3-week lag (r = 0.75), humidity
at 2 weeks and temperature at ~8 weeks.

Every lag / rolling window is computed with groupby("ward_id") on data sorted by date, so a
ward's features only ever use that same ward's PAST rows (tests/test_features.py checks this).

Forecast framing ("direct multi-horizon"):
    issue week t  = the latest week we have data for (what an officer knows today)
    target week   = t + h, h in {2, 3, 4}
    features      = only values from weeks <= t
    label         = dengue_cases at week t + h
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RAIN_LAGS = range(0, 9)        # rainfall 0..8 weeks before the issue week
CLIMATE_LAGS = range(0, 5)     # temperature & humidity 0..4 weeks before
CASE_LAGS = range(0, 4)        # dengue cases 0..3 weeks before
STATIC_COLS = ["population_density_sqkm", "water_logging_risk_index", "sanitation_score"]
CITIES = ["Chattogram", "Dhaka North", "Dhaka South", "Gazipur", "Narayanganj"]


def _roll(g, col: str, window: int, how: str) -> pd.Series:
    """Rolling stat over the last `window` weeks INCLUDING the issue week, per ward."""
    return g[col].transform(lambda s: getattr(s.rolling(window, min_periods=window), how)())


def build_base_features(df: pd.DataFrame) -> pd.DataFrame:
    """Horizon-independent features for every (ward, issue week) row."""
    df = df.sort_values(["ward_id", "date"]).reset_index(drop=True)
    g = df.groupby("ward_id", sort=False)
    f: dict[str, pd.Series] = {}

    # --- Rainfall: the breeding clock -------------------------------------------------
    for k in RAIN_LAGS:
        f[f"rain_lag_{k}w"] = g["rainfall_mm"].shift(k)
    for w in (2, 4, 8):
        f[f"rain_sum_{w}w"] = _roll(g, "rainfall_mm", w, "sum")
    f["heavy_rain_weeks_4w"] = (df["rainfall_mm"] > 50).astype(float).groupby(df["ward_id"]).transform(
        lambda s: s.rolling(4, min_periods=4).sum()
    )

    # --- Temperature & humidity --------------------------------------------------------
    for k in CLIMATE_LAGS:
        f[f"temp_lag_{k}w"] = g["avg_temp_c"].shift(k)
        f[f"humidity_lag_{k}w"] = g["relative_humidity_pct"].shift(k)
    for w in (4, 8):
        f[f"temp_mean_{w}w"] = _roll(g, "avg_temp_c", w, "mean")
        f[f"humidity_mean_{w}w"] = _roll(g, "relative_humidity_pct", w, "mean")
    f["temp_range_lag_0w"] = df["max_temp_c"] - df["min_temp_c"]

    # --- Hyper-local vulnerability x weather -------------------------------------------
    for c in STATIC_COLS:
        f[c] = df[c]
    f["rain4w_x_waterlogging"] = f["rain_sum_4w"] * df["water_logging_risk_index"]
    f["rain4w_x_poor_sanitation"] = f["rain_sum_4w"] * (1 - df["sanitation_score"])
    for city in CITIES:
        f[f"city_{city.replace(' ', '_')}"] = (df["city"] == city).astype(float)

    # --- Recent case history (what surveillance already knows at issue time) ------------
    for k in CASE_LAGS:
        f[f"cases_lag_{k}w"] = g["dengue_cases"].shift(k)
    f["cases_mean_4w"] = _roll(g, "dengue_cases", 4, "mean")
    f["cases_trend_2w"] = f["cases_lag_0w"] - f["cases_lag_2w"]

    feats = pd.DataFrame(f)
    meta = df[["date", "city", "ward_id", "dengue_cases"]]
    return pd.concat([meta, feats], axis=1)


def add_horizon_features(base: pd.DataFrame, h: int) -> pd.DataFrame:
    """Add target + target-week features for horizon h (weeks ahead)."""
    out = base.copy()
    g = out.groupby("ward_id", sort=False)
    out["horizon_weeks"] = h
    out["target_date"] = out["date"] + pd.to_timedelta(7 * h, unit="D")
    # Seasonality of the week we are predicting (known in advance: it's the calendar)
    woy = out["target_date"].dt.isocalendar().week.astype(float)
    out["target_week_sin"] = np.sin(2 * np.pi * woy / 52.0)
    out["target_week_cos"] = np.cos(2 * np.pi * woy / 52.0)
    # Same calendar week last year (52 weeks before the target week -> already in the past)
    out["cases_same_week_last_year"] = g["dengue_cases"].shift(52 - h)
    # Label: cases h weeks after the issue week (NaN for the last h weeks -> live forecasts)
    out["target"] = g["dengue_cases"].shift(-h)
    return out


CASE_HISTORY_COLS = [f"cases_lag_{k}w" for k in CASE_LAGS] + [
    "cases_mean_4w", "cases_trend_2w", "cases_same_week_last_year",
]
META_COLS = ["date", "city", "ward_id", "dengue_cases", "horizon_weeks", "target_date", "target"]


def feature_columns(frame: pd.DataFrame, weather_only: bool = False) -> list[str]:
    cols = [c for c in frame.columns if c not in META_COLS]
    if weather_only:
        cols = [c for c in cols if c not in CASE_HISTORY_COLS]
    return cols


def make_supervised(df: pd.DataFrame, h: int, base: pd.DataFrame | None = None) -> pd.DataFrame:
    """Full feature table for horizon h. Rows lacking 52 weeks of history are dropped."""
    base = build_base_features(df) if base is None else base
    out = add_horizon_features(base, h)
    cols = feature_columns(out)
    return out.dropna(subset=cols).reset_index(drop=True)
