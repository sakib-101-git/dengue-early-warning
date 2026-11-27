"""Dengue Watch JSON API + static frontend.

    python -m server                 # http://127.0.0.1:8000
    uvicorn server.api:app           # same app, your own uvicorn flags

The RiskEngine (models + feature tables) is built once at startup and the full ward x week
forecast tables for every horizon are precomputed, so each request is a cheap pandas lookup.
"""
from __future__ import annotations

import bisect
import json
import logging
from contextlib import asynccontextmanager
from datetime import date as Date
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.config import HORIZONS, METRICS_JSON, TEST_YEARS, TIER_LABELS
from src.predict import BREEDING, OLDER, RiskEngine
from src import resources as res

log = logging.getLogger("dengue_watch")
WEB_DIR = Path(__file__).resolve().parents[1] / "web"

DEFAULTS = {"city": "Dhaka North", "ward": "DNCC_Ward_01", "h": 3, "date": "2025-07-07"}
RAIN_WEEKS = 12  # weeks of rainfall shown in the ward hero

# Tier colours used by the frontend (light / dark). Kept here so the UI never invents its own scale.
TIER_COLORS = {
    "Low": {"light": "#16A34A", "dark": "#4ADE80"},
    "Medium": {"light": "#D97706", "dark": "#FBBF24"},
    "High": {"light": "#EA580C", "dark": "#FB923C"},
    "Critical": {"light": "#DC2626", "dark": "#F87171"},
}

PLAIN_DRIVER = {
    BREEDING: "Rain 2–4 weeks before",
    OLDER: "Earlier rain (5+ weeks)",
    "Rain x ward vulnerability": "Rain on flood-prone ground",
    "Temperature": "Temperature",
    "Humidity": "Humidity",
    "Ward: water-logging risk": "Ward drainage",
    "Ward: population density": "Crowding",
    "Ward: sanitation": "Waste collection",
    "Season (calendar week)": "Time of year",
    "City": "City",
    "Recent case history": "Recent cases",
}

MODEL_LABELS = {
    "Baseline: persistence (cases now)": "Copy this week",
    "Baseline: same week last year": "Same week last year",
    "Poisson GLM (statistical)": "Poisson regression",
    "Random Forest": "Random forest",
    "XGBoost (Poisson)": "XGBoost",
    "LightGBM (Poisson)  <- deployed": "LightGBM (ours)",
}

FEATURE_LABELS = {
    "rain_sum_2w": "Rain, last 2 weeks",
    "rain_sum_4w": "Rain, last 4 weeks",
    "rain_sum_8w": "Rain, last 8 weeks",
    "heavy_rain_weeks_4w": "Heavy-rain weeks, last 4",
    "target_week_cos": "Time of year (cos)",
    "target_week_sin": "Time of year (sin)",
    "rain4w_x_waterlogging": "Rain × water-logging",
    "rain4w_x_poor_sanitation": "Rain × poor sanitation",
    "temp_mean_4w": "Temperature, 4-week mean",
    "temp_mean_8w": "Temperature, 8-week mean",
    "humidity_mean_4w": "Humidity, 4-week mean",
    "humidity_mean_8w": "Humidity, 8-week mean",
    "temp_range_lag_0w": "Day–night temperature range",
    "population_density_sqkm": "Population density",
    "water_logging_risk_index": "Water-logging risk",
    "sanitation_score": "Sanitation score",
}


def short_ward(ward_id: str) -> str:
    return "Ward " + ward_id.rsplit("_", 1)[-1]


def feature_label(col: str) -> str:
    if col in FEATURE_LABELS:
        return FEATURE_LABELS[col]
    for prefix, name in (("rain_lag_", "Rain"), ("temp_lag_", "Temperature"), ("humidity_lag_", "Humidity")):
        if col.startswith(prefix):
            k = int(col[len(prefix):].rstrip("w"))
            return f"{name}, {k} week{'s' if k != 1 else ''} ago" if k else f"{name}, this week"
    return col.replace("_", " ")


def iso(d) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")


def num(x, nd: int = 2):
    return None if x is None or pd.isna(x) else round(float(x), nd)


# ---------------------------------------------------------------- state (built once)
class State:
    def __init__(self) -> None:
        self.engine = RiskEngine()
        self.tables = {h: self.engine.forecast_table(h) for h in HORIZONS}
        self.weeks = {h: sorted(pd.to_datetime(t.issue_date.unique())) for h, t in self.tables.items()}
        raw = self.engine.raw
        self.cities = sorted(raw.city.unique())
        self.wards = {c: sorted(raw.loc[raw.city == c, "ward_id"].unique()) for c in self.cities}
        self.ward_city = {w: c for c, ws in self.wards.items() for w in ws}
        self.metrics = json.loads(Path(METRICS_JSON).read_text(encoding="utf-8"))
        # per-ward slices so single-ward lookups don't scan 30k rows
        self.by_ward = {h: {w: g for w, g in t.groupby("ward_id")} for h, t in self.tables.items()}
        self.raw_by_ward = {w: g.sort_values("date") for w, g in raw.groupby("ward_id")}
        self.baseline_mae = self._same_week_last_year_mae()
        # Phase 2: supply rates (fitted on training years, cached JSON) + static ward profiles.
        self.profiles = res.ward_profiles(raw)
        try:
            self.rates = res.load_rates(raw)
        except Exception:  # noqa: BLE001 - no cache and no supply columns: allocation is disabled
            log.exception("Supply rates unavailable")
            self.rates = None

    def _same_week_last_year_mae(self) -> float:
        r = self.engine.raw.sort_values(["ward_id", "date"])
        last = r.groupby("ward_id").dengue_cases.shift(52)
        m = r.date.dt.year.isin(TEST_YEARS) & last.notna()
        return float((r.loc[m, "dengue_cases"] - last[m]).abs().mean())

    def snap(self, h: int, d: Date | None) -> pd.Timestamp:
        """Latest issue week <= d, clamped to the valid range."""
        weeks = self.weeks[h]
        if d is None:
            return pd.Timestamp(DEFAULTS["date"])
        i = bisect.bisect_right(weeks, pd.Timestamp(d)) - 1
        return weeks[min(max(i, 0), len(weeks) - 1)]


STATE: State | None = None


def S() -> State:
    if STATE is None:  # pragma: no cover - only if lifespan didn't run
        raise HTTPException(503, "Model is still loading, try again in a moment.")
    return STATE


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global STATE
    if STATE is None:
        STATE = State()
    yield


app = FastAPI(title="Dengue Watch API", version="1.0", lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json")


@app.exception_handler(RequestValidationError)
async def bad_params(_req: Request, exc: RequestValidationError):
    """Bad query parameters -> 400 with one readable sentence (e.g. 'date: use YYYY-MM-DD')."""
    msgs = []
    for err in exc.errors():
        field = str(err.get("loc", ["", "?"])[-1])
        if field == "date":
            msgs.append("date must look like 2025-07-07 (YYYY-MM-DD).")
        else:
            msgs.append(f"{field}: {err.get('msg', 'invalid value')}.")
    return JSONResponse({"detail": " ".join(msgs) or "Invalid request."}, status_code=400)


@app.exception_handler(Exception)
async def unhandled(_req: Request, exc: Exception):  # never leak a traceback to the UI
    log.exception("Unhandled error", exc_info=exc)
    return JSONResponse({"detail": "Something went wrong on the server. Please try again."}, status_code=500)


# ---------------------------------------------------------------- validation helpers
def check_h(h: int) -> int:
    if h not in HORIZONS:
        raise HTTPException(400, f"Weeks ahead must be one of {', '.join(map(str, HORIZONS))}.")
    return h


def check_city(city: str) -> str:
    if city not in S().wards:
        raise HTTPException(400, f"Unknown city '{city}'. Choose one of: {', '.join(S().cities)}.")
    return city


def check_ward(ward: str) -> str:
    if ward not in S().ward_city:
        raise HTTPException(400, f"Unknown ward '{ward}'.")
    return ward


def forecast_json(fc) -> dict:
    return {
        "ward": fc.ward_id, "ward_label": short_ward(fc.ward_id), "city": fc.city, "horizon": fc.horizon,
        "issue_date": iso(fc.issue_date), "target_date": iso(fc.target_date),
        "expected": num(fc.expected_cases), "low_80": num(fc.low_80), "high_80": num(fc.high_80),
        "tier": fc.tier, "actual": num(fc.actual, 0),
    }


# ---------------------------------------------------------------- endpoints
@app.get("/api/health")
def health():
    return {"status": "ok", "ready": STATE is not None}


@app.get("/api/meta")
def meta():
    s = S()
    lo = max(w[0] for w in s.weeks.values())
    hi = min(w[-1] for w in s.weeks.values())
    th = {h: s.metrics["horizons"][str(h)]["tier_thresholds_cases"] for h in HORIZONS}
    return {
        "cities": [{"name": c, "wards": [{"id": w, "label": short_ward(w)} for w in s.wards[c]]} for c in s.cities],
        "min_date": iso(lo), "max_date": iso(hi),
        "horizons": list(HORIZONS), "tiers": list(TIER_LABELS), "tier_colors": TIER_COLORS,
        "tier_thresholds": th, "default": DEFAULTS,
        "years": sorted({int(y) for y in s.tables[DEFAULTS["h"]].target_date.dt.year.unique()}),
    }


@lru_cache(maxsize=512)
def _ward_payload(ward: str, issue: pd.Timestamp, h: int, rain: int, temp: int, hum: int) -> dict:
    s = S()
    e = s.engine
    sim = bool(rain or temp or hum)
    frame = e.what_if_frame(ward, issue, h, rain, temp, hum) if sim else None
    real = e.forecast(ward, issue, h)
    fc = e.forecast(ward, issue, h, frame=frame) if sim else real

    base, grouped, _ = e.explain(ward, issue, h, frame=frame)
    drivers = [{"key": g, "label": PLAIN_DRIVER.get(g, g), "log_effect": num(le, 4), "pct_effect": num(pe, 1)}
               for g, le, pe in grouped[["group", "log_effect", "pct_effect"]].itertuples(index=False)]

    target = pd.Timestamp(fc.target_date)
    rw = s.raw_by_ward[ward]
    rw = rw[rw.date <= issue].tail(RAIN_WEEKS)[["date", "rainfall_mm"]]
    mm = rw.rainfall_mm.to_numpy(dtype=float)
    if sim:
        mm = frame.set_index("date").loc[rw.date, "rain_lag_0w"].to_numpy(dtype=float)
    lag = ((target - rw.date).dt.days // 7).to_numpy()
    rain = [{"date": iso(d), "mm": round(float(v), 1), "weeks_before_target": int(k), "breeding": bool(2 <= k <= 4)}
            for d, v, k in zip(rw.date, mm, lag)]
    ahead = [{"date": iso(issue + pd.Timedelta(weeks=i)), "weeks_before_target": h - i, "breeding": 2 <= h - i <= 4}
             for i in range(1, h)]

    hist = s.by_ward[h][ward]
    hist = hist[hist.target_date.dt.year == target.year]
    season = [{"date": iso(r.target_date), "expected": num(r.expected_cases), "low": num(r.low_80),
               "high": num(r.high_80), "actual": num(r.actual, 0), "tier": r.tier} for r in hist.itertuples()]

    return {
        "forecast": forecast_json(fc), "baseline": forecast_json(real), "simulated": sim,
        "simulation": {"rain": rain, "temp": temp, "hum": hum} if sim else None,
        "typical_cases": num(base), "drivers": drivers,
        "rain": rain, "ahead": ahead, "season": season,
        "thresholds": s.metrics["horizons"][str(h)]["tier_thresholds_cases"],
    }


@app.get("/api/ward")
def ward(ward: str = Query(...), date: Date | None = None, h: int = 3,
         rain: int = Query(0, ge=-80, le=150), temp: int = Query(0, ge=-4, le=4), hum: int = Query(0, ge=-20, le=20)):
    check_ward(ward)
    check_h(h)
    issue = S().snap(h, date)
    return _ward_payload(ward, issue, h, rain, temp, hum)


@lru_cache(maxsize=256)
def _city_payload(city: str, issue: pd.Timestamp, h: int) -> dict:
    t = S().tables[h]
    now = t[(t.city == city) & (t.issue_date == issue)].sort_values("expected_cases", ascending=False)
    counts = {k: int((now.tier == k).sum()) for k in TIER_LABELS}
    target = issue + pd.Timedelta(weeks=h)
    return {
        "city": city, "horizon": h, "issue_date": iso(issue), "target_date": iso(target),
        "n_wards": int(len(now)), "need_action": counts["High"] + counts["Critical"], "tier_counts": counts,
        "wards": [{"rank": i + 1, "ward": r.ward_id, "label": short_ward(r.ward_id), "expected": num(r.expected_cases),
                   "low_80": num(r.low_80), "high_80": num(r.high_80), "tier": r.tier, "actual": num(r.actual, 0)}
                  for i, r in enumerate(now.itertuples())],
    }


@app.get("/api/city")
def city(city: str = Query(...), date: Date | None = None, h: int = 3):
    check_city(city)
    check_h(h)
    return _city_payload(city, S().snap(h, date), h)


@lru_cache(maxsize=128)
def _heatmap_payload(city: str, year: int, h: int) -> dict:
    t = S().tables[h]
    hm = t[(t.city == city) & (t.target_date.dt.year == year)]
    if hm.empty:
        raise HTTPException(400, f"No forecasts for {city} in {year}.")
    weeks = sorted(hm.target_date.unique())
    wards = S().wards[city]
    tier = hm.pivot(index="ward_id", columns="target_date", values="tier").reindex(index=wards, columns=weeks)
    val = hm.pivot(index="ward_id", columns="target_date", values="expected_cases").reindex(index=wards, columns=weeks)
    act = hm.pivot(index="ward_id", columns="target_date", values="actual").reindex(index=wards, columns=weeks)
    tier_idx = {k: i for i, k in enumerate(TIER_LABELS)}
    cells = []
    for i, w in enumerate(wards):
        for j, d in enumerate(weeks):
            tv = tier.iat[i, j]
            if isinstance(tv, str):
                cells.append([j, i, tier_idx[tv], round(float(val.iat[i, j]), 1), num(act.iat[i, j], 0)])
    return {"city": city, "year": year, "horizon": h, "weeks": [iso(d) for d in weeks],
            "wards": [{"id": w, "label": short_ward(w)} for w in wards], "tiers": list(TIER_LABELS),
            "cells": cells}


@app.get("/api/heatmap")
def heatmap(city: str = Query(...), year: int = Query(..., ge=2000, le=2100), h: int = 3):
    check_city(city)
    check_h(h)
    return _heatmap_payload(city, year, h)


@app.get("/api/model")
def model(h: int = 3):
    check_h(h)
    s = S()
    m = s.metrics
    hm = m["horizons"][str(h)]
    comp = [{"key": k, "label": MODEL_LABELS.get(k, k), "ours": "deployed" in k, **v}
            for k, v in hm["validation_comparison"].items()]
    return {
        "horizon": h, "deployed_model": hm.get("deployed_model"), "n_trees": hm.get("n_trees"),
        "test": hm["test"], "coverage_80": hm["test_interval_coverage_80"],
        "baseline_same_week_mae": round(s.baseline_mae, 3),
        "gain_vs_same_week": round(s.baseline_mae / hm["test"]["MAE"], 2),
        "comparison": comp,
        "backtest": {str(k): m["horizons"][str(k)]["backtest_by_year"] for k in HORIZONS},
        "top_features": [{"feature": k, "label": feature_label(k), "share": v}
                         for k, v in list(hm["top_features_gain_share"].items())[:10]],
        "thresholds": hm["tier_thresholds_cases"], "split": m["split"],
        "test_with_case_history": hm.get("test_with_case_history"),
    }


# ---------------------------------------------------------------- Phase 2: resource allocation
def check_scope(scope: str) -> str:
    if scope.lower() == "all":
        return "all"
    if scope not in S().wards:
        raise HTTPException(400, f"Unknown scope '{scope}'. Use 'all' or one of: {', '.join(S().cities)}.")
    return scope


def check_range(name: str, v: int, lo: int, hi: int) -> int:
    if not lo <= v <= hi:
        raise HTTPException(400, f"{name} must be between {lo} and {hi} (got {v}).")
    return v


def need_rates() -> dict:
    if S().rates is None:
        raise HTTPException(503, "Supply rates are unavailable: the onsite dataset or models/resource_rates.json is missing.")
    return S().rates


def scope_forecast(scope: str, issue: pd.Timestamp, h: int) -> pd.DataFrame:
    t = S().tables[h]
    now = t[t.issue_date == issue]
    return now if scope == "all" else now[now.city == scope]


def supply_json(r, key: str) -> dict:
    return {"required": int(getattr(r, f"{key}_required")), "safety": int(getattr(r, f"{key}_safety")),
            "allocated": int(getattr(r, f"{key}_allocated")), "deficit": int(getattr(r, f"{key}_deficit")),
            "coverage": round(float(getattr(r, f"{key}_coverage")), 4)}


@lru_cache(maxsize=256)
def _allocation_payload(scope: str, issue: pd.Timestamp, h: int, stock: int, crews: int, floor: int) -> dict:
    rates = need_rates()
    plan = res.allocate(scope_forecast(scope, issue, h), S().profiles, rates, stock, crews, floor)
    d = plan["wards"]
    wards, alerts = [], []
    for r in d.itertuples():
        sup = {k: supply_json(r, k) for k in res.SUPPLY_KEYS}
        row = {"priority": int(r.priority), "ward": r.ward_id, "label": short_ward(r.ward_id), "city": r.city,
               "tier": r.tier, "expected": num(r.expected_cases), "low_80": num(r.low_80), "high_80": num(r.high_80),
               "density": int(r.density), "density_weight": num(r.density_weight, 3), "score": num(r.score, 3),
               "supplies": sup, "crews_needed": int(r.crews_needed), "crews_assigned": int(r.crews_assigned),
               "flag": r.flag or None}
        wards.append(row)
        if r.flag:
            short = [{"supply": k, **v} for k, v in sup.items() if v["deficit"] > 0]
            alerts.append({
                "level": r.flag, "ward": r.ward_id, "label": short_ward(r.ward_id), "city": r.city, "tier": r.tier,
                "priority": int(r.priority), "short": short,
                "message": f"{r.flag}: {short_ward(r.ward_id)}, {r.city} ({r.tier} risk). " + "; ".join(
                    f"{rates['supplies'][x['supply']]['label'].lower()} {x['allocated']}/{x['required']} "
                    f"({round(100 * x['coverage'])}%)" for x in short) + ".",
            })
    return {
        "scope": scope, "scope_label": "All cities" if scope == "all" else scope,
        "horizon": h, "issue_date": iso(issue), "target_date": iso(issue + pd.Timedelta(weeks=h)),
        "stock_pct": stock, "floor_pct": floor, "n_wards": len(wards),
        "supplies": [{**s, "k": rates["supplies"][s["key"]]["k"], "r2_test": rates["supplies"][s["key"]]["r2_test"],
                      "mae_test": rates["supplies"][s["key"]]["mae_test"]} for s in res.SUPPLIES],
        "totals": plan["totals"], "crews": plan["crews"],
        "n_critical_deficit": sum(a["level"] == res.CRITICAL_DEFICIT for a in alerts),
        "n_warning": sum(a["level"] == res.WARNING for a in alerts),
        "alerts": alerts, "wards": wards,
        "priority_rule": res.PRIORITY_RULE,
        "rates_fit": {k: rates[k] for k in ("method", "fit_years", "test_years")},
    }


@app.get("/api/allocation")
def allocation(scope: str = "all", date: Date | None = None, h: int = 3, stock_pct: int = 100,
               crews: int = 20, floor: int = 0):
    scope = check_scope(scope)
    check_h(h)
    check_range("stock_pct", stock_pct, *res.STOCK_RANGE)
    check_range("crews", crews, 0, 1000)
    if floor not in res.FLOOR_OPTIONS:
        raise HTTPException(400, f"floor must be one of {', '.join(map(str, res.FLOOR_OPTIONS))} (got {floor}).")
    return _allocation_payload(scope, S().snap(h, date), h, stock_pct, crews, floor)


@app.get("/api/{rest:path}")
def api_404(rest: str):
    raise HTTPException(404, f"No API endpoint /api/{rest}")


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
