"""Dengue Watch JSON API + static frontend.

    python -m server                 # http://127.0.0.1:8000
    uvicorn server.api:app           # same app, your own uvicorn flags

The RiskEngine (models + feature tables) is built once at startup and the full ward x week
forecast tables for every horizon are precomputed, so each request is a cheap pandas lookup.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import logging
import mimetypes
from contextlib import asynccontextmanager
from datetime import date as Date
from email.utils import formatdate
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from src.config import HORIZONS, METRICS_JSON, TEST_YEARS, TIER_LABELS
from src.predict import BREEDING, OLDER, RiskEngine
from src import dispatch
from src import hospitals as hosp
from src import resources as res
from src import simulator as sim

log = logging.getLogger("dengue_watch")
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
mimetypes.add_type("font/woff2", ".woff2")   # not in every OS mime table (e.g. Windows)

DEFAULTS = {"city": "Dhaka North", "ward": "DNCC_Ward_01", "h": 3, "date": "2025-07-07"}
RAIN_WEEKS = 12  # weeks of rainfall shown in the ward hero

# Response caches: every one is a bounded LRU, sized so that all of them full add < ~50 MB to the
# ~330 MB the app needs after startup (Render free tier: 512 MB). Measured retained size per entry
# (tracemalloc): ward 40 KB, city 9, heatmap 106, allocation 124, overflow 74, simulator curves 386,
# simulate 87, rank 19-60, dispatch 79. Everything except the curves (~0.5 s) recomputes in milliseconds.
CACHE = {"ward": 128, "city": 128, "heatmap": 64, "allocation": 64, "overflow": 64, "curves": 16,
         "simulate": 64, "rank": 64, "dispatch": 64}

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
        # the simulator forecasts h = 2, 3 and 4 at once, so it needs weeks valid at every horizon
        self.sim_weeks = sorted(set.intersection(*(set(w) for w in self.weeks.values())))
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
        try:
            self.bed_capacity = hosp.baseline_capacity(raw, self.rates)
        except Exception:  # noqa: BLE001 - neither the bed column nor a bed rate: overflow is disabled
            log.exception("Bed capacity unavailable")
            self.bed_capacity = None

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
# Files the app needs to run with no network at all: models, data, supply rates and the vendored
# front-end assets (charts library + fonts). Nothing else is fetched at runtime.
VENDORED = ("vendor/echarts.min.js", "vendor/fonts/Geist-Variable.woff2", "vendor/fonts/GeistMono-Variable.woff2")


def _versions() -> dict:
    import platform
    from importlib.metadata import PackageNotFoundError, version
    out = {"python": platform.python_version()}
    for pkg in ("numpy", "pandas", "scikit-learn", "lightgbm", "fastapi", "uvicorn"):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:  # pragma: no cover
            out[pkg] = None
    return out


@lru_cache(maxsize=1)
def _static_health() -> dict:
    """Checks that cannot change while the process runs (computed once)."""
    from src.config import MODELS_DIR, RAW_CSV, RESOURCE_RATES_JSON
    models = {f"h{h}": (MODELS_DIR / f"model_h{h}.joblib").is_file() for h in HORIZONS}
    assets = {p: (WEB_DIR / p).is_file() for p in ("index.html", "styles.css", "js/app.js", *VENDORED)}
    return {"models": models, "data_file": RAW_CSV.name if RAW_CSV.is_file() else None,
            "rates_file": RESOURCE_RATES_JSON.is_file(), "assets": assets, "versions": _versions()}


@app.get("/api/health")
def health():
    """Liveness + offline readiness. offline_ready = every component loaded from local files, so
    forecasting, allocation, routing, simulation and dispatch all work with the network unplugged.
    (UptimeRobot pings GET / or this endpoint; both stay plain 200s.)"""
    st = _static_health()
    s = STATE
    components = {
        "models": s is not None and all(st["models"].values()) and len(s.engine.bundles) == len(HORIZONS),
        "data": s is not None and len(s.ward_city) == 60,
        "forecast_tables": s is not None and all(len(t) for t in s.tables.values()),
        "supply_rates": s is not None and s.rates is not None,
        "bed_capacity": s is not None and s.bed_capacity is not None,
        "web_assets": all(st["assets"].values()),
        "dispatch_templates": True,   # pure Python string templates (src/dispatch.py), no network or LLM
    }
    return {"status": "ok", "ready": s is not None, "offline_ready": all(components.values()),
            "components": components, "data_file": st["data_file"], "assets": st["assets"],
            "versions": st["versions"]}


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


@lru_cache(maxsize=CACHE["ward"])
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


@lru_cache(maxsize=CACHE["city"])
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


@lru_cache(maxsize=CACHE["heatmap"])
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
def check_scope(scope: str, name: str = "scope") -> str:
    if scope.lower() == "all":
        return "all"
    if scope not in S().wards:
        raise HTTPException(400, f"Unknown {name} '{scope}'. Use 'all' or one of: {', '.join(S().cities)}.")
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


@lru_cache(maxsize=CACHE["allocation"])
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


# ---------------------------------------------------------------- Phase 2: hospital surge + overflow
def ward_beds_json(r) -> dict:
    return {
        "ward": r.ward_id, "label": short_ward(r.ward_id), "city": r.city, "tier": r.tier,
        "expected": num(r.expected_cases), "high_80": num(r.high_80),
        "baseline_capacity": int(r.baseline_capacity), "capacity": int(r.capacity),
        "demand": int(r.demand), "demand_expected": int(r.demand_expected), "demand_surge": int(r.demand_surge),
        "deficit": int(r.deficit), "headroom": int(r.headroom), "utilisation": num(r.utilisation, 4),
        "sent": int(r.sent), "received": int(r.received), "load_after": int(r.load_after),
        "net_deficit": int(r.net_deficit), "headroom_after": int(r.headroom_after),
        "utilisation_after": num(r.utilisation_after, 4), "status": r.status,
        "can_receive": r.tier not in hosp.NO_RECEIVE_TIERS,
    }


@lru_cache(maxsize=CACHE["overflow"])
def _overflow_payload(city: str, issue: pd.Timestamp, h: int, cap: int, basis: str) -> dict:
    rates = need_rates()
    if S().bed_capacity is None:
        raise HTTPException(503, "Bed capacity is unavailable: the dataset has no bed column and no bed rate.")
    plan = hosp.plan_overflow(scope_forecast(city, issue, h), S().bed_capacity, rates, cap, basis)
    d = plan["wards"].sort_values(["city", "utilisation", "ward_id"], ascending=[True, False, True], kind="mergesort")
    cities = [{"city": c, **v} for c, v in plan["cities"].items()]
    tot = {k: sum(c[k] for c in cities) for k in ("n_wards", "demand", "capacity", "overflow_before", "wards_over",
                                                  "transferred", "unresolved", "wards_unresolved", "headroom_before",
                                                  "remaining_headroom", "headroom_reserved")}
    tot["utilisation"] = round(tot["demand"] / tot["capacity"], 4) if tot["capacity"] else 0.0
    return {
        "city": city, "scope_label": "All cities" if city == "all" else city,
        "horizon": h, "issue_date": iso(issue), "target_date": iso(issue + pd.Timedelta(weeks=h)),
        "capacity_pct": cap, "basis": basis, "k_beds": rates["supplies"]["beds"]["k"],
        "totals": tot, "cities": cities,
        "transfers": [{**t, "from_label": short_ward(t["from"]), "to_label": short_ward(t["to"])} for t in plan["transfers"]],
        "wards": [ward_beds_json(r) for r in d.itertuples()],
        "capacity_rule": hosp.CAPACITY_RULE, "routing_rule": hosp.ROUTING_RULE,
    }


@app.get("/api/overflow")
def overflow(city: str = "all", date: Date | None = None, h: int = 3, capacity_pct: int = 100,
             basis: str = "expected"):
    city = check_scope(city, "city")
    check_h(h)
    check_range("capacity_pct", capacity_pct, *hosp.CAPACITY_RANGE)
    if basis not in hosp.BASES:
        raise HTTPException(400, f"basis must be one of {', '.join(hosp.BASES)} (got '{basis}').")
    return _overflow_payload(city, S().snap(h, date), h, capacity_pct, basis)


# ---------------------------------------------------------------- Phase 2: vector control simulator
MAX_CREWS_TOTAL = 1000


def snap_sim(d: Date | None) -> pd.Timestamp:
    """Latest issue week <= d that has forecasts at every horizon, clamped to the valid range."""
    weeks = S().sim_weeks
    t = pd.Timestamp(DEFAULTS["date"]) if d is None else pd.Timestamp(d)
    i = bisect.bisect_right(weeks, t) - 1
    return weeks[min(max(i, 0), len(weeks) - 1)]


def scope_wards(scope: str) -> tuple[str, ...]:
    s = S()
    return tuple(sorted(s.ward_city)) if scope == "all" else tuple(s.wards[scope])


def parse_crews(value) -> tuple[tuple[str, int], ...]:
    """Crew plan from JSON ({"DNCC_Ward_01": 2}) or compact text ("DNCC_Ward_01:2,DSCC_Ward_03:1")."""
    if value is None or value == "":
        return ()
    obj = value
    if isinstance(value, str):
        txt = value.strip()
        if txt.startswith("{"):
            try:
                obj = json.loads(txt)
            except ValueError:
                raise HTTPException(400, "crews must be JSON like {\"DNCC_Ward_01\": 2} or text like DNCC_Ward_01:2.")
        else:
            obj = {}
            for part in filter(None, (p.strip() for p in txt.split(","))):
                w, sep, n = part.partition(":")
                if not sep:
                    raise HTTPException(400, f"crews: '{part}' should look like DNCC_Ward_01:2.")
                obj[w.strip()] = n.strip()
    if not isinstance(obj, dict):
        raise HTTPException(400, "crews must map ward ids to crew counts.")
    out: dict[str, int] = {}
    for w, n in obj.items():
        check_ward(str(w))
        try:
            if isinstance(n, bool) or (isinstance(n, float) and not n.is_integer()):
                raise ValueError
            n = int(n)
        except (TypeError, ValueError):
            raise HTTPException(400, f"Crews for {w} must be a whole number (got {n!r}).")
        check_range(f"Crews for {w}", n, 0, sim.MAX_CREWS_PER_WARD)
        if n:
            out[str(w)] = n
    if sum(out.values()) > MAX_CREWS_TOTAL:
        raise HTTPException(400, f"At most {MAX_CREWS_TOTAL} crews in total.")
    return tuple(sorted(out.items()))


@lru_cache(maxsize=CACHE["curves"])
def _curves(issue: pd.Timestamp, effect: int) -> sim.Curves:
    """All wards x h2-h4 x crew levels for one issue week: one batched model run, then lookups."""
    return sim.Curves(sim.response_curves(S().engine, issue, effect), effect)


def sim_point_json(p: dict) -> dict:
    return {"cases": num(p["cases"]), "low_80": num(p["low_80"]), "high_80": num(p["high_80"]), "tier": p["tier"],
            **{k: int(p[k]) for k in sim.SIM_SUPPLIES}}


def sim_common(scope: str, issue: pd.Timestamp, effect: int, cv: sim.Curves) -> dict:
    return {"scope": scope, "scope_label": "All cities" if scope == "all" else scope, "issue_date": iso(issue),
            "effect_pct": effect, "cap_pct": round(sim.CAP * 100), "max_useful_crews": cv.max_crews,
            "horizons": [{"h": int(h), "target_date": iso(cv.target_dates[int(h)])} for h in cv.horizons],
            "method": sim.METHOD}


@lru_cache(maxsize=CACHE["simulate"])
def _simulate_payload(scope: str, issue: pd.Timestamp, effect: int, crews: tuple, auto: int | None) -> dict:
    rates = need_rates()
    cv = _curves(issue, effect)
    pool = list(scope_wards(scope))
    plan = sim.auto_deploy(cv, auto, pool) if auto is not None else dict(crews)
    r = sim.simulate(cv, plan, rates, pool)
    wards = [{
        "ward": w["ward_id"], "label": short_ward(w["ward_id"]), "city": w["city"], "crews": w["crews"],
        "effective_crews": w["effective_crews"], "reduction": num(w["reduction"], 4),
        "tier": w["tier"], "tier_after": w["tier_after"], "roi": num(w["roi"], 3),
        "prevented": {"cases": num(w["prevented"]["cases"], 3), **{k: int(w["prevented"][k]) for k in sim.SIM_SUPPLIES}},
        "horizons": [{"h": x["h"], "target_date": iso(x["target_date"]), "baseline": sim_point_json(x["baseline"]),
                      "scenario": sim_point_json(x["scenario"]),
                      "prevented": {"cases": num(x["prevented"]["cases"], 3),
                                    **{k: int(x["prevented"][k]) for k in sim.SIM_SUPPLIES}}} for x in w["horizons"]],
    } for w in r["wards"]]
    by_h = [{k: (iso(v) if k == "target_date" else num(v, 3) if "cases" in k else v) for k, v in row.items()}
            for row in r["by_horizon"]]
    deployed = r["crews_deployed"]
    return {
        **sim_common(scope, issue, effect, cv),
        "mode": "auto" if auto is not None else "manual", "auto_requested": auto,
        "crews": dict(sorted(plan.items())), "crews_deployed": deployed, "wards_deployed": r["wards_deployed"],
        "crews_spare": (auto - deployed) if auto is not None else 0,
        "totals": {"by_horizon": by_h,
                   "prevented": {"cases": num(r["prevented"]["cases"], 3), **{k: int(r["prevented"][k]) for k in sim.SIM_SUPPLIES}},
                   "roi": num(r["roi"], 3), "scope_baseline_cases": num(r["scope_baseline_cases"], 2),
                   "share_of_scope": num(r["share_of_scope"], 4)},
        "wards": wards,
        "rates": {k: rates["supplies"][k]["k"] for k in sim.SIM_SUPPLIES},
    }


def check_effect(effect: int) -> int:
    return check_range("effect", effect, *sim.EFFECT_RANGE)


def check_auto(auto: int | None) -> int | None:
    return None if auto is None else check_range("auto", auto, 0, MAX_CREWS_TOTAL)


@app.get("/api/simulate")
def simulate_get(scope: str = "all", date: Date | None = None, effect: int = sim.EFFECT_DEFAULT,
                 crews: str = "", auto: int | None = None):
    """Crew plan as ?crews={"DNCC_Ward_01":2} (or DNCC_Ward_01:2,...), or ?auto=N for greedy auto-deploy."""
    scope = check_scope(scope)
    return _simulate_payload(scope, snap_sim(date), check_effect(effect), parse_crews(crews), check_auto(auto))


@app.post("/api/simulate")
def simulate_post(body: dict = Body(default_factory=dict)):
    """Same as GET, with a JSON body: {"crews": {ward_id: crews}, "date", "effect", "scope", "auto"}."""
    scope = check_scope(str(body.get("scope", "all")))
    d = body.get("date")
    try:
        day = Date.fromisoformat(str(d)) if d else None
    except ValueError:
        raise HTTPException(400, "date must look like 2025-07-07 (YYYY-MM-DD).")
    try:
        effect = int(body.get("effect", sim.EFFECT_DEFAULT))
        auto = None if body.get("auto") is None else int(body["auto"])
    except (TypeError, ValueError):
        raise HTTPException(400, "effect and auto must be whole numbers.")
    return _simulate_payload(scope, snap_sim(day), check_effect(effect), parse_crews(body.get("crews") or {}),
                             check_auto(auto))


@lru_cache(maxsize=CACHE["rank"])
def _rank_payload(scope: str, issue: pd.Timestamp, effect: int) -> dict:
    cv = _curves(issue, effect)
    d = sim.rank(cv, list(scope_wards(scope)))
    return {
        **sim_common(scope, issue, effect, cv),
        "wards": [{"rank": int(r.rank), "ward": r.ward_id, "label": short_ward(r.ward_id), "city": r.city, "tier": r.tier,
                   "baseline_cases": num(r.baseline_cases, 3),
                   "baseline_by_h": {str(h): num(getattr(r, f"baseline_h{h}"), 3) for h in cv.horizons},
                   "roi_1": num(r.roi_1, 3), "best_roi": num(r.best_roi, 3), "best_roi_crews": int(r.best_roi_crews),
                   "prevented_max": num(r.prevented_max, 3), "crews_max": int(r.crews_max)} for r in d.itertuples()],
    }


@app.get("/api/simulate/rank")
def simulate_rank(scope: str = "all", date: Date | None = None, effect: int = sim.EFFECT_DEFAULT):
    """Every ward in scope ranked by 1-crew ROI (cases prevented over h2-h4 by one crew)."""
    return _rank_payload(check_scope(scope), snap_sim(date), check_effect(effect))


# ---------------------------------------------------------------- Phase 2: multi-channel dispatch
DISPATCH_POOLS = ("city", "all")
DISPATCH_MEDIA = {"sms": "text/plain; charset=utf-8", "whatsapp": "text/plain; charset=utf-8",
                  "brief": "text/html; charset=utf-8"}


def dispatch_plan(plan: str, alloc: dict) -> tuple:
    """'' = no crew impact, 'allocation' = the crews Resources assigned, else a simulator plan (ward:crews,...)."""
    if plan in ("", "none"):
        return ()
    if plan == "allocation":
        return tuple(sorted((w["ward"], w["crews_assigned"]) for w in alloc["wards"] if w["crews_assigned"] > 0))
    return parse_crews(plan)


@lru_cache(maxsize=CACHE["dispatch"])
def _dispatch_payload(scope: str, ident: str, issue: pd.Timestamp, h: int, stock: int, crews: int, floor: int,
                      cap: int, basis: str, pool: str, plan: str, effect: int, sim_issue: pd.Timestamp,
                      method: bool | None = None) -> dict:
    city = ident if scope == "city" else S().ward_city[ident]
    alloc = _allocation_payload(city if pool == "city" else "all", issue, h, stock, crews, floor)
    over = _overflow_payload(city, issue, h, cap, basis)
    crew_plan = dispatch_plan(plan, alloc)
    simp = _simulate_payload("all", sim_issue, effect, crew_plan, None) if crew_plan else None
    out = dispatch.build(scope, ident, alloc, over, simp, method=method)
    out["plan"] = "allocation" if plan == "allocation" else ("simulator" if crew_plan else "none")
    return out


@app.get("/api/dispatch")
def dispatch_notice(scope: str = "ward", id: str = DEFAULTS["ward"], date: Date | None = None, h: int = 3,
                    stock_pct: int = 100, crews: int = 20, floor: int = 0, capacity_pct: int = 100,
                    basis: str = "expected", pool: str = "city", plan: str = "", effect: int = sim.EFFECT_DEFAULT,
                    method: int | None = None, format: str = "json"):
    """Ward or city notice as SMS / WhatsApp text, a printable HTML brief, or JSON with all three.

    Numbers come from the same cached payloads as /api/allocation (scope = the city, or all wards with
    pool=all), /api/overflow (the city) and, with plan=allocation or plan=ward:crews,..., /api/simulate.
    method=1|0 adds or drops the brief's plain-language method page (default: on for city, off for ward).
    """
    if scope not in dispatch.SCOPES:
        raise HTTPException(400, f"scope must be one of {', '.join(dispatch.SCOPES)} (got '{scope}').")
    if format not in dispatch.FORMATS:
        raise HTTPException(400, f"format must be one of {', '.join(dispatch.FORMATS)} (got '{format}').")
    if scope == "ward":
        check_ward(id)
    else:
        check_city(id)
    check_h(h)
    check_range("stock_pct", stock_pct, *res.STOCK_RANGE)
    check_range("crews", crews, 0, 1000)
    if floor not in res.FLOOR_OPTIONS:
        raise HTTPException(400, f"floor must be one of {', '.join(map(str, res.FLOOR_OPTIONS))} (got {floor}).")
    check_range("capacity_pct", capacity_pct, *hosp.CAPACITY_RANGE)
    if basis not in hosp.BASES:
        raise HTTPException(400, f"basis must be one of {', '.join(hosp.BASES)} (got '{basis}').")
    if pool not in DISPATCH_POOLS:
        raise HTTPException(400, f"pool must be one of {', '.join(DISPATCH_POOLS)} (got '{pool}').")
    check_effect(effect)
    if method not in (None, 0, 1):
        raise HTTPException(400, f"method must be 0 or 1 (got {method}).")
    plan = plan.strip()
    if plan not in ("", "none", "allocation"):
        plan = ",".join(f"{w}:{n}" for w, n in parse_crews(plan))   # validate + canonical cache key
    out = _dispatch_payload(scope, id, S().snap(h, date), h, stock_pct, crews, floor, capacity_pct, basis, pool,
                            plan, effect, snap_sim(date), None if method is None else bool(method))
    if format == "json":
        return out
    return Response(out[format], media_type=DISPATCH_MEDIA[format])


@app.get("/api/{rest:path}")
def api_404(rest: str):
    raise HTTPException(404, f"No API endpoint /api/{rest}")


class WebFiles(StaticFiles):
    """Static UI with explicit caching.

    The app's own files (index.html, styles.css, js/*) change with every deploy and are ES modules that
    import each other, so they are served with `Cache-Control: no-cache`: the browser keeps a copy but
    revalidates it (ETag / Last-Modified -> 304) on every load. On top of that, index.html loads them by
    content-hashed URLs (see render_index), so a copy cached under older rules is never reused.
    Vendored third-party files (vendor/: ECharts, fonts) only change when the vendored version is bumped
    and may be cached for a week.
    """
    VENDOR_MAX_AGE = 7 * 24 * 3600

    async def get_response(self, path: str, scope):
        resp = await super().get_response(path, scope)
        p = path.replace("\\", "/").lstrip("./")
        resp.headers["Cache-Control"] = (f"public, max-age={self.VENDOR_MAX_AGE}" if p.startswith("vendor/")
                                         else "no-cache")
        return resp


# ---------------------------------------------------------------- cache-busting for the app's own files
# index.html is served with a short content hash on every app file it loads: styles.css?v=…, the entry
# module js/app.js?v=…, and an import map that pins every other module under js/ to its own ?v=… URL.
# A browser can therefore never pair a new page with an old stylesheet or module, even one it cached
# before the no-cache headers existed: changed content = new URL. Vendored files keep plain, cacheable URLs.
APP_ASSETS = ("styles.css", "js/**/*.js")


@lru_cache(maxsize=64)
def _content_hash(path: str, mtime_ns: int, size: int) -> str:
    """Short SHA-256 of a file's bytes; keyed on mtime/size so an edited file gets a new hash."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:10]


def asset_versions(web_dir: Path = WEB_DIR) -> dict[str, str]:
    """{path relative to web/ (posix): content hash} for styles.css and every module under js/."""
    out = {}
    for pattern in APP_ASSETS:
        for f in sorted(web_dir.glob(pattern)):
            st = f.stat()
            out[f.relative_to(web_dir).as_posix()] = _content_hash(str(f), st.st_mtime_ns, st.st_size)
    return out


def render_index(web_dir: Path = WEB_DIR) -> str:
    """index.html with versioned URLs for the stylesheet, the entry module and (via an import map) every
    module it imports. The file's own bytes (line endings included) are otherwise unchanged."""
    v = asset_versions(web_dir)
    html = (web_dir / "index.html").read_bytes().decode("utf-8")
    css, entry = 'href="styles.css"', '<script type="module" src="js/app.js"></script>'
    if css not in html or entry not in html:              # never serve a page that could load stale files
        raise RuntimeError("index.html must link styles.css and load js/app.js as its module entry")
    imports = {f"./{k}": f"./{k}?v={h}" for k, h in v.items() if k.startswith("js/")}
    importmap = json.dumps({"imports": imports}, separators=(",", ":"))
    html = html.replace(css, f'href="styles.css?v={v["styles.css"]}"', 1)
    return html.replace(entry, f'<script type="importmap">{importmap}</script>'
                               f'<script type="module" src="js/app.js?v={v["js/app.js"]}"></script>', 1)


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index_page(request: Request):
    """The versioned index: always revalidated (no-cache), with an ETag so an unchanged build is a 304."""
    html = render_index()
    newest = max(f.stat().st_mtime for f in [WEB_DIR / "index.html", *(WEB_DIR / k for k in asset_versions())])
    headers = {"Cache-Control": "no-cache", "Last-Modified": formatdate(newest, usegmt=True),
               "ETag": '"' + hashlib.sha256(html.encode("utf-8")).hexdigest()[:20] + '"'}
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    return HTMLResponse(html, headers=headers)


app.mount("/", WebFiles(directory=WEB_DIR, html=True), name="web")
