"""Hospital surge: derived bed capacity, per-ward deficits and greedy intra-city overflow routing.

1. Capacity (ASSUMPTION - the dataset has no capacity column). Baseline beds per ward =
       max(2, ceil(P95 of that ward's weekly hospital_beds_required in training years <= 2022))
   i.e. a ward's hospital can absorb a bad-but-normal week. A capacity scale (50-200%) multiplies it:
       capacity = max(1, round_half_up(baseline x scale%)).
2. Demand for the target week = round(k_beds x cases), with k_beds the learned per-case bed rate
   (src.resources). basis="expected" uses the point forecast; basis="surge" uses the upper end of
   the 80% range (q90), the conservative figure for bed planning.
3. Per ward: deficit = max(demand - capacity, 0), headroom = max(capacity - demand, 0).
4. Routing, per city and NEVER across cities (no coordinates exist, so it is by headroom and
   burden, not distance). Repeat until no deficit or no eligible headroom is left:
       sender   = ward with the largest remaining deficit (ties: ward id)
       receiver = non-Critical ward with headroom; most headroom first, then lowest utilisation,
                  then ward id
       move min(deficit, headroom) patients sender -> receiver
5. Output: transfer list, per-ward net deficit after routing, and per-city totals
   (demand, capacity, utilisation, remaining headroom, unresolved overflow).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.config import TRAIN_END_YEAR
from src.resources import rate_k, units

BEDS_COLUMN = "hospital_beds_required"
CAPACITY_QUANTILE = 0.95
MIN_CAPACITY = 2
CAPACITY_RANGE = (50, 200)        # capacity scale slider, %
BASES = ("expected", "surge")
BASIS_COLUMN = {"expected": "expected_cases", "surge": "high_80"}
NO_RECEIVE_TIERS = ("Critical",)  # a Critical ward keeps its spare beds for its own surge

CAPACITY_RULE = (
    "Assumed capacity: the dataset has no bed-capacity column, so each ward's baseline is "
    f"max({MIN_CAPACITY}, ceil(P95 of its weekly hospital_beds_required in training years "
    f"<= {TRAIN_END_YEAR})), the beds it needed in a bad-but-normal week. The capacity scale "
    "multiplies this baseline."
)
ROUTING_RULE = (
    "Overflow is routed within the same city only. The ward with the largest deficit sends patients "
    "first, to the non-Critical ward with the most spare beds (then the lowest utilisation). "
    "There are no ward coordinates, so routing uses headroom and burden, not distance."
)


def baseline_capacity(raw: pd.DataFrame, rates: dict | None = None) -> pd.Series:
    """Baseline beds per ward (index ward_id). Uses the bed column, or k_beds x cases if it's absent."""
    tr = raw[raw["date"].dt.year <= TRAIN_END_YEAR]
    if BEDS_COLUMN in tr.columns:
        beds = tr[BEDS_COLUMN].astype(float)
    elif rates is not None:
        beds = pd.Series(units(rate_k(rates)["beds"], tr["dengue_cases"]), index=tr.index, dtype=float)
    else:
        raise ValueError("Cannot derive bed capacity: no hospital_beds_required column and no bed rate.")
    p95 = beds.groupby(tr["ward_id"]).quantile(CAPACITY_QUANTILE)
    return p95.apply(lambda v: max(MIN_CAPACITY, math.ceil(v - 1e-9))).astype(int).rename("baseline_capacity")


def scale_capacity(baseline, pct: float) -> np.ndarray:
    """max(1, round-half-up(baseline x pct%)); deterministic, no banker's rounding surprises."""
    b = np.asarray(baseline, dtype=float)
    return np.maximum(1, np.floor(b * pct / 100 + 0.5)).astype(int)


def route_city(d: pd.DataFrame) -> list[dict]:
    """Greedy routing inside ONE city. d: ward_id, tier, demand, capacity. Returns transfers."""
    ids = d["ward_id"].tolist()
    demand = d["demand"].to_numpy(int)
    cap = d["capacity"].to_numpy(int)
    deficit = np.maximum(demand - cap, 0)
    head = np.maximum(cap - demand, 0)
    can_receive = ~d["tier"].isin(NO_RECEIVE_TIERS).to_numpy()
    load = demand.astype(float)                      # current occupancy, for the utilisation tie-break
    transfers: list[dict] = []
    while True:
        senders = np.flatnonzero(deficit > 0)
        receivers = np.flatnonzero((head > 0) & can_receive)
        if not len(senders) or not len(receivers):
            break
        s = min(senders, key=lambda i: (-deficit[i], ids[i]))
        r = min(receivers, key=lambda i: (-head[i], load[i] / cap[i], ids[i]))
        n = int(min(deficit[s], head[r]))
        deficit[s] -= n
        head[r] -= n
        load[r] += n
        transfers.append({"from": ids[s], "to": ids[r], "patients": n})
    return transfers


def plan_overflow(fc: pd.DataFrame, baseline: pd.Series, rates: dict, capacity_pct: float = 100,
                  basis: str = "expected") -> dict:
    """Overflow plan for any set of wards (one or more cities) and one target week.

    fc needs: ward_id, city, tier, expected_cases, high_80.
    Returns {"wards": DataFrame, "transfers": [...], "cities": {city: {...}}}.
    """
    if basis not in BASES:
        raise ValueError(f"basis must be one of {BASES}")
    k = rate_k(rates)["beds"]
    d = fc[["ward_id", "city", "tier", "expected_cases", "high_80"]].copy()
    d["baseline_capacity"] = d["ward_id"].map(baseline).astype(int)
    d["capacity"] = scale_capacity(d["baseline_capacity"], capacity_pct)
    d["demand"] = units(k, d[BASIS_COLUMN[basis]])
    d["demand_expected"] = units(k, d["expected_cases"])
    d["demand_surge"] = np.maximum(units(k, d["high_80"]), d["demand_expected"])
    d["deficit"] = np.maximum(d["demand"] - d["capacity"], 0)
    d["headroom"] = np.maximum(d["capacity"] - d["demand"], 0)
    d["utilisation"] = d["demand"] / d["capacity"]
    d = d.sort_values(["city", "ward_id"], kind="mergesort").reset_index(drop=True)

    transfers: list[dict] = []
    for city, g in d.groupby("city", sort=True):
        for t in route_city(g):
            transfers.append({**t, "city": city})

    sent = pd.Series(0, index=d["ward_id"])
    got = pd.Series(0, index=d["ward_id"])
    for t in transfers:
        sent[t["from"]] += t["patients"]
        got[t["to"]] += t["patients"]
    d["sent"] = sent.to_numpy()
    d["received"] = got.to_numpy()
    d["load_after"] = d["demand"] - d["sent"] + d["received"]
    d["net_deficit"] = np.maximum(d["load_after"] - d["capacity"], 0)
    d["headroom_after"] = np.maximum(d["capacity"] - d["load_after"], 0)
    d["utilisation_after"] = d["load_after"] / d["capacity"]
    d["status"] = np.select(
        [d["net_deficit"] > 0, d["sent"] > 0, d["received"] > 0, d["utilisation"] >= 0.85],
        ["unresolved", "relieved", "receiving", "near capacity"], "ok")

    cities = {}
    for city, g in d.groupby("city", sort=True):
        dem, cap = int(g["demand"].sum()), int(g["capacity"].sum())
        cities[city] = {
            "n_wards": int(len(g)), "demand": dem, "capacity": cap,
            "utilisation": round(dem / cap, 4) if cap else 0.0,
            "overflow_before": int(g["deficit"].sum()), "wards_over": int((g["deficit"] > 0).sum()),
            "transferred": int(g["sent"].sum()),
            "unresolved": int(g["net_deficit"].sum()), "wards_unresolved": int((g["net_deficit"] > 0).sum()),
            "headroom_before": int(g["headroom"].sum()), "remaining_headroom": int(g["headroom_after"].sum()),
            "headroom_reserved": int(g.loc[g["tier"].isin(NO_RECEIVE_TIERS), "headroom_after"].sum()),
        }
    return {"wards": d, "transfers": transfers, "cities": cities}
