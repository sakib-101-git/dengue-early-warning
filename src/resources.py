"""Constrained resource allocation: forecast cases -> supplies, priority, scarce-stock fill, crews.

    python -m src.resources          # (re)fit the supply rates and print them

1. Supply rates (learned, not hardcoded). In the onsite dataset every supply column is almost
   exactly a per-case ratio, so one rate k per supply is fitted by least squares through the
   origin (units = k x cases) on TRAINING years only (<= TRAIN_END_YEAR). Test-year (2024-25)
   R2 / MAE / exact-match are reported next to k in models/resource_rates.json.
2. Requirement for a ward-week = round(k x expected cases). A "safety" figure uses the q90
   forecast (upper end of the 80% range) instead.
3. Priority: tier rank first (Critical > High > Medium > Low), then
   score = expected_cases x density_weight, density_weight = density / city median, clipped 0.5-2.
4. Allocation per supply, deterministic greedy fill down the priority list:
       available = floor(stock% x total required in scope)
       phase 1: Critical wards are filled completely, in priority order
       phase 2 (optional floor): every other ward is topped up to floor% of its requirement
       phase 3: whatever is left goes down the priority list until it runs out
5. Flags: CRITICAL DEFICIT = High/Critical ward with any supply below 100%; WARNING = any other
   ward below 100%.
6. Crews: needed = Low 0 / Medium 1 / High 2 / Critical 3, +1 for wards in the top 25% density
   of their city (only for wards that need spraying at all, i.e. Medium and above). The total
   crew pool is assigned down the same priority list.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import RESOURCE_RATES_JSON, TEST_YEARS, TRAIN_END_YEAR
from src.data import SUPPLY_COLUMNS

# Short keys used by the API / UI, mapped to the dataset columns.
SUPPLIES = (
    {"key": "saline", "column": "saline_bags_needed", "label": "Saline bags", "unit": "bags"},
    {"key": "kits", "column": "diagnostic_kits_needed", "label": "Diagnostic kits", "unit": "kits"},
    {"key": "beds", "column": "hospital_beds_required", "label": "Hospital beds", "unit": "beds"},
)
SUPPLY_KEYS = tuple(s["key"] for s in SUPPLIES)

TIER_RANK = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
CREWS_BY_TIER = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
DENSITY_WEIGHT_CLIP = (0.5, 2.0)
DENSE_QUANTILE = 0.75          # "top 25% density of its city"
STOCK_RANGE = (30, 100)        # Available Regional Stock slider, %
FLOOR_OPTIONS = (0, 20)        # minimum-coverage floor, %
CRITICAL_DEFICIT = "CRITICAL DEFICIT"
WARNING = "WARNING"

PRIORITY_RULE = (
    "Wards are served in priority order: risk tier first (Critical > High > Medium > Low), then "
    "expected cases x density weight, where density weight = ward density / city median density, "
    "clipped to 0.5-2.0. Critical wards are filled first; the rest of the stock goes down the list."
)


# ---------------------------------------------------------------- 1. supply rates
def fit_rates(raw: pd.DataFrame) -> dict:
    """Fit units = k x cases through the origin on training years; score on the test years."""
    missing = [c for c in SUPPLY_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"Cannot fit supply rates: dataset lacks {missing} (use the onsite CSV).")
    year = raw["date"].dt.year
    train, test = raw[year <= TRAIN_END_YEAR], raw[year.isin(TEST_YEARS)]
    out = {
        "method": "least squares through the origin: units = k x dengue_cases",
        "fit_years": f"<= {TRAIN_END_YEAR}", "test_years": list(TEST_YEARS),
        "n_train": int(len(train)), "n_test": int(len(test)), "supplies": {},
    }
    for s in SUPPLIES:
        x, y = train["dengue_cases"].to_numpy(float), train[s["column"]].to_numpy(float)
        k = float((x * y).sum() / (x * x).sum())
        xt, yt = test["dengue_cases"].to_numpy(float), test[s["column"]].to_numpy(float)
        pred = k * xt
        r2 = 1 - ((yt - pred) ** 2).sum() / ((yt - yt.mean()) ** 2).sum()
        r2_train = 1 - ((y - k * x) ** 2).sum() / ((y - y.mean()) ** 2).sum()
        out["supplies"][s["key"]] = {
            "column": s["column"], "label": s["label"], "unit": s["unit"],
            "k": round(k, 6), "r2_train": round(float(r2_train), 4), "r2_test": round(float(r2), 4),
            "mae_test": round(float(np.abs(yt - np.round(pred)).mean()), 4),
            "exact_match_test": round(float((np.round(pred) == yt).mean()), 4),
        }
    return out


def load_rates(raw: pd.DataFrame | None = None, path: Path = RESOURCE_RATES_JSON, refit: bool = False) -> dict:
    """Cached rates from JSON; fit (and write the cache) when missing or when refit=True."""
    path = Path(path)
    if path.exists() and not refit:
        rates = json.loads(path.read_text(encoding="utf-8"))
        if set(rates.get("supplies", {})) >= set(SUPPLY_KEYS):
            return rates
    if raw is None:
        from src.data import load_raw
        raw = load_raw()
    rates = fit_rates(raw)
    path.write_text(json.dumps(rates, indent=2) + "\n", encoding="utf-8")
    return rates


def rate_k(rates: dict) -> dict[str, float]:
    return {k: float(rates["supplies"][k]["k"]) for k in SUPPLY_KEYS}


def units(k: float, cases) -> np.ndarray:
    """round(k x cases), as non-negative ints (round-half-even, same as the dataset check)."""
    return np.round(k * np.clip(np.asarray(cases, dtype=float), 0, None)).astype(int)


# ---------------------------------------------------------------- 2. ward profiles + priority
def ward_profiles(raw: pd.DataFrame) -> pd.DataFrame:
    """Static per-ward facts: density, density weight vs city median, top-25% density flag."""
    p = raw.groupby("ward_id", as_index=False).agg(city=("city", "first"),
                                                 density=("population_density_sqkm", "median"))
    med = p.groupby("city")["density"].transform("median")
    q = p.groupby("city")["density"].transform(lambda s: s.quantile(DENSE_QUANTILE))
    p["density_weight"] = (p["density"] / med).clip(*DENSITY_WEIGHT_CLIP)
    p["dense_top25"] = p["density"] > q
    return p.set_index("ward_id")


def prioritise(fc: pd.DataFrame, profiles: pd.DataFrame) -> pd.DataFrame:
    """Add density info + priority score and sort. fc: ward_id, city, expected_cases, high_80, tier."""
    d = fc.join(profiles[["density", "density_weight", "dense_top25"]], on="ward_id")
    d["tier_rank"] = d["tier"].map(TIER_RANK).astype(int)
    d["score"] = d["expected_cases"] * d["density_weight"]
    d = d.sort_values(["tier_rank", "score", "ward_id"], ascending=[False, False, True], kind="mergesort")
    d["priority"] = np.arange(1, len(d) + 1)
    return d.reset_index(drop=True)


def crews_needed(tier: pd.Series, dense_top25: pd.Series) -> np.ndarray:
    base = tier.map(CREWS_BY_TIER).astype(int).to_numpy()
    return base + ((base > 0) & dense_top25.to_numpy()).astype(int)


# ---------------------------------------------------------------- 3. greedy fill
def greedy_fill(required: np.ndarray, available: int, critical: np.ndarray, floor_pct: int = 0) -> np.ndarray:
    """Allocate `available` units to wards already sorted by priority. Returns allocated units.

    Never gives a ward more than it needs, never hands out more than is available.
    """
    req = np.asarray(required, dtype=int)
    alloc = np.zeros_like(req)
    left = int(max(available, 0))

    def give(i: int, want: int) -> None:
        nonlocal left
        g = min(max(want, 0), left)
        alloc[i] += g
        left -= g

    for i in np.flatnonzero(critical):                       # phase 1: Critical wards, fully
        give(i, req[i] - alloc[i])
    if floor_pct:                                            # phase 2: floor for everyone else
        for i in np.flatnonzero(~np.asarray(critical)):
            give(i, math.ceil(req[i] * floor_pct / 100) - alloc[i])
    for i in range(len(req)):                                # phase 3: down the priority list
        give(i, req[i] - alloc[i])
    return alloc


def assign_crews(needed: np.ndarray, total: int) -> np.ndarray:
    """Crews down the priority list (wards sorted by priority)."""
    out = np.zeros_like(np.asarray(needed, dtype=int))
    left = int(max(total, 0))
    for i, n in enumerate(needed):
        out[i] = min(int(n), left)
        left -= out[i]
    return out


# ---------------------------------------------------------------- 4. the whole plan
def allocate(fc: pd.DataFrame, profiles: pd.DataFrame, rates: dict, stock_pct: float = 100,
             crews_total: int = 0, floor_pct: int = 0) -> dict:
    """Constrained allocation for one scope (any set of wards) and one target week.

    fc needs: ward_id, city, expected_cases, low_80, high_80, tier.
    Returns {"wards": DataFrame sorted by priority, "totals": {...}, "crews": {...}}.
    """
    d = prioritise(fc, profiles)
    k = rate_k(rates)
    critical = (d["tier"] == "Critical").to_numpy()
    totals = {}
    short_any = np.zeros(len(d), dtype=bool)
    for s in SUPPLY_KEYS:
        req = units(k[s], d["expected_cases"])
        avail = int(math.floor(stock_pct / 100 * req.sum() + 1e-9))
        alloc = greedy_fill(req, avail, critical, floor_pct)
        d[f"{s}_required"] = req
        d[f"{s}_safety"] = np.maximum(units(k[s], d["high_80"]), req)
        d[f"{s}_allocated"] = alloc
        d[f"{s}_deficit"] = req - alloc
        d[f"{s}_coverage"] = np.where(req > 0, alloc / np.maximum(req, 1), 1.0)
        short = alloc < req
        short_any |= short
        totals[s] = {
            "required": int(req.sum()), "safety": int(d[f"{s}_safety"].sum()), "available": avail,
            "allocated": int(alloc.sum()), "unmet": int((req - alloc).sum()),
            "coverage": round(float(alloc.sum() / req.sum()), 4) if req.sum() else 1.0,
            "wards_short": int(short.sum()),
        }
    high = d["tier"].isin(["High", "Critical"]).to_numpy()
    d["flag"] = np.where(short_any & high, CRITICAL_DEFICIT, np.where(short_any, WARNING, ""))

    need = crews_needed(d["tier"], d["dense_top25"])
    got = assign_crews(need, crews_total)
    d["crews_needed"], d["crews_assigned"] = need, got
    crews = {"total": int(crews_total), "needed": int(need.sum()), "assigned": int(got.sum()),
             "spare": int(crews_total - got.sum()), "unmet": int((need - got).sum()),
             "wards_short": int((got < need).sum())}
    return {"wards": d, "totals": totals, "crews": crews}


if __name__ == "__main__":  # pragma: no cover
    r = load_rates(refit=True)
    for key, v in r["supplies"].items():
        print(f"{v['label']:<16} k={v['k']:.4f}  R2(test)={v['r2_test']:.4f}  "
              f"MAE(test)={v['mae_test']:.3f}  exact={v['exact_match_test']:.2%}")
    print(f"written to {RESOURCE_RATES_JSON}")
