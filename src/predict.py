"""Inference engine used by the dashboard.

Loads the serialised models once, builds features once, and answers:
  - forecast_table(h): predictions + 80% interval + tier for every ward and issue week
  - explain(...):      which weather / ward drivers pushed this prediction up or down
  - what_if(...):      re-run one ward's prediction with modified recent weather
No network calls, no LLM: everything runs locally in milliseconds.
"""
from __future__ import annotations

from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd

from src.config import HORIZONS, MODELS_DIR
from src.data import load_raw
from src.features import add_horizon_features, build_base_features
from src.tiers import assign_tier

# Human-readable driver groups for the explanation panel.
# Rain features are grouped by how many weeks BEFORE THE TARGET WEEK the rain fell
# (issue-week lag k + horizon h), so the panel speaks the brief's "2-to-4-week" language.
BREEDING = "Rain 2-4 weeks before target week (breeding window)"
OLDER = "Rain 5+ weeks before target week"
STATIC_GROUPS = [
    ("Rain x ward vulnerability", lambda c: c.startswith("rain4w_x")),
    ("Temperature", lambda c: c.startswith("temp")),
    ("Humidity", lambda c: c.startswith("humidity")),
    ("Ward: water-logging risk", lambda c: c == "water_logging_risk_index"),
    ("Ward: population density", lambda c: c == "population_density_sqkm"),
    ("Ward: sanitation", lambda c: c == "sanitation_score"),
    ("Season (calendar week)", lambda c: c.startswith("target_week")),
    ("City", lambda c: c.startswith("city_")),
    ("Recent case history", lambda c: c.startswith("cases")),
]


def driver_group(col: str, h: int) -> str:
    if col.startswith("rain_lag_"):
        lag_to_target = int(col.split("_")[2].rstrip("w")) + h
        return BREEDING if lag_to_target <= 4 else OLDER
    if col.startswith(("rain_sum_", "heavy_rain_weeks_")):
        window = int(col.split("_")[-1].rstrip("w"))
        mid_lag = h + (window - 1) / 2  # centre of the window, in weeks before target
        return BREEDING if mid_lag <= 4.5 else OLDER
    for name, rule in STATIC_GROUPS:
        if rule(col):
            return name
    return "Other"


@dataclass
class Forecast:
    ward_id: str
    city: str
    issue_date: pd.Timestamp
    target_date: pd.Timestamp
    horizon: int
    expected_cases: float
    low_80: float
    high_80: float
    tier: str
    actual: float | None


class RiskEngine:
    def __init__(self, raw: pd.DataFrame | None = None):
        self.raw = load_raw() if raw is None else raw
        self.bundles = {h: joblib.load(MODELS_DIR / f"model_h{h}.joblib") for h in HORIZONS}
        self.base = build_base_features(self.raw)
        self._frames = {h: add_horizon_features(self.base, h) for h in HORIZONS}
        self._tables: dict[int, pd.DataFrame] = {}

    # ------------------------------------------------------------------ batch
    def forecast_table(self, h: int) -> pd.DataFrame:
        """Vectorised predictions for every (ward, issue week) with full feature history."""
        if h not in self._tables:
            b, fr = self.bundles[h], self._frames[h]
            fr = fr.dropna(subset=b["features"]).copy()
            X = fr[b["features"]]
            fr["expected_cases"] = np.clip(b["model"].predict(X), 0, None)
            fr["low_80"] = np.clip(b["q10"].predict(X), 0, None)
            fr["high_80"] = np.maximum(b["q90"].predict(X), fr["expected_cases"])
            fr["tier"] = assign_tier(fr["expected_cases"], b["thresholds"])
            fr["actual_tier"] = np.where(fr["target"].notna(),
                                         assign_tier(fr["target"].fillna(0), b["thresholds"]), None)
            keep = ["date", "target_date", "city", "ward_id", "expected_cases", "low_80",
                    "high_80", "tier", "target", "actual_tier"]
            self._tables[h] = fr[keep].rename(columns={"date": "issue_date", "target": "actual"})
        return self._tables[h]

    # ------------------------------------------------------------------ single
    def _row(self, ward: str, issue_date, h: int, frame: pd.DataFrame | None = None) -> pd.DataFrame:
        fr = self._frames[h] if frame is None else frame
        row = fr[(fr.ward_id == ward) & (fr.date == pd.Timestamp(issue_date))]
        if row.empty:
            raise KeyError(f"No data for {ward} at {issue_date}")
        return row

    def forecast(self, ward: str, issue_date, h: int, frame: pd.DataFrame | None = None) -> Forecast:
        b = self.bundles[h]
        row = self._row(ward, issue_date, h, frame)
        X = row[b["features"]]
        mu = float(np.clip(b["model"].predict(X)[0], 0, None))
        lo = float(np.clip(b["q10"].predict(X)[0], 0, None))
        hi = float(max(b["q90"].predict(X)[0], mu))
        actual = row["target"].iloc[0]
        return Forecast(ward, row["city"].iloc[0], row["date"].iloc[0], row["target_date"].iloc[0],
                        h, mu, lo, hi, str(assign_tier([mu], b["thresholds"])[0]),
                        None if pd.isna(actual) else float(actual))

    def explain(self, ward: str, issue_date, h: int, frame: pd.DataFrame | None = None):
        """Per-feature contributions (LightGBM's built-in TreeSHAP, log scale for Poisson).

        Returns (baseline_cases, grouped DataFrame with % effect on expected cases, raw table).
        """
        b = self.bundles[h]
        X = self._row(ward, issue_date, h, frame)[b["features"]]
        contrib = b["model"].predict(X, pred_contrib=True)[0]
        base_log, feats = contrib[-1], contrib[:-1]
        raw = pd.DataFrame({"feature": b["features"], "value": X.iloc[0].values, "log_effect": feats})
        raw["group"] = raw["feature"].map(lambda c: driver_group(c, h))
        grouped = raw.groupby("group", as_index=False)["log_effect"].sum()
        grouped["pct_effect"] = (np.exp(grouped["log_effect"]) - 1) * 100
        grouped = grouped.reindex(grouped["log_effect"].abs().sort_values(ascending=False).index)
        return float(np.exp(base_log)), grouped, raw.sort_values("log_effect", key=abs, ascending=False)

    def feature_rows(self, issue_date, h: int) -> pd.DataFrame:
        """Model-ready feature rows of every ward at one issue week (sorted by ward)."""
        b, fr = self.bundles[h], self._frames[h]
        rows = fr[fr.date == pd.Timestamp(issue_date)].dropna(subset=b["features"])
        if rows.empty:
            raise KeyError(f"No feature rows at {issue_date} for h={h}")
        return rows.sort_values("ward_id")

    # ------------------------------------------------------------------ what-if
    def what_if_frame(self, ward: str, issue_date, h: int, rain_pct: float = 0.0,
                      temp_delta: float = 0.0, humidity_delta: float = 0.0, weeks: int = 4,
                      waterlog_pct: float = 0.0):
        """Rebuild ONE ward's features after changing the last `weeks` weeks of weather.

        waterlog_pct cuts the ward's water_logging_risk_index by that % in the same window
        (vector control: crews removing breeding sites; see src/simulator.py).
        """
        w = self.raw[self.raw.ward_id == ward].copy()
        t = pd.Timestamp(issue_date)
        win = (w.date <= t) & (w.date > t - pd.Timedelta(weeks=weeks))
        w.loc[win, "rainfall_mm"] *= 1 + rain_pct / 100
        if waterlog_pct:
            w.loc[win, "water_logging_risk_index"] *= 1 - waterlog_pct / 100
        for c in ("avg_temp_c", "max_temp_c", "min_temp_c"):
            w.loc[win, c] += temp_delta
        w.loc[win, "relative_humidity_pct"] = (w.loc[win, "relative_humidity_pct"] + humidity_delta).clip(0, 100)
        return add_horizon_features(build_base_features(w), h)
