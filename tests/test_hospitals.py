"""Hospital surge + overflow routing: capacity rule, greedy routing, conservation, city isolation, API."""
import math

import numpy as np
import pandas as pd
import pytest

from src import config
from src import hospitals as hosp
from src.data import load_raw

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from server.api import app  # noqa: E402

DATE = "2025-07-07"
CITIES = ["Chattogram", "Dhaka North", "Dhaka South", "Gazipur", "Narayanganj"]
# Scenarios that actually produce overflow on the demo week (checked: deficits > 0).
BUSY = [dict(capacity_pct=75), dict(capacity_pct=70), dict(basis="surge"), dict(basis="surge", capacity_pct=120)]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def get(client, **params):
    r = client.get("/api/overflow", params={"date": DATE, "h": 3, **params})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- capacity rule
@pytest.mark.skipif(not config.ONSITE_CSV.exists(), reason="onsite CSV not present")
def test_baseline_capacity_is_p95_of_training_years():
    raw = load_raw(config.ONSITE_CSV)
    cap = hosp.baseline_capacity(raw)
    assert len(cap) == 60 and (cap >= hosp.MIN_CAPACITY).all()
    tr = raw[raw.date.dt.year <= config.TRAIN_END_YEAR]
    for w in ["DNCC_Ward_01", "CCC_Ward_05", "NCC_Ward_03"]:
        p95 = tr.loc[tr.ward_id == w, "hospital_beds_required"].quantile(0.95)
        assert cap[w] == max(2, math.ceil(p95))


def test_scale_capacity_rounds_half_up_and_never_zero():
    assert hosp.scale_capacity([4, 3, 2, 5], 50).tolist() == [2, 2, 1, 3]
    assert hosp.scale_capacity([4, 3], 100).tolist() == [4, 3]
    assert hosp.scale_capacity([4, 3], 200).tolist() == [8, 6]
    assert hosp.scale_capacity([1], 50).tolist() == [1]


# ---------------------------------------------------------------- pure routing
def toy(rows):
    return pd.DataFrame(rows, columns=["ward_id", "tier", "demand", "capacity"])


def test_route_city_largest_deficit_to_most_headroom():
    d = toy([("A", "High", 9, 4),       # deficit 5
             ("B", "High", 6, 4),       # deficit 2
             ("C", "Medium", 1, 5),     # headroom 4
             ("D", "Low", 1, 3),        # headroom 2
             ("E", "Critical", 0, 9)])  # headroom 9 but Critical: never receives
    t = hosp.route_city(d)
    assert t[0] == {"from": "A", "to": "C", "patients": 4}
    assert all(x["to"] != "E" for x in t)
    assert sum(x["patients"] for x in t) == 6          # all 6 spare non-Critical beds used, 1 unresolved
    sent = {w: sum(x["patients"] for x in t if x["from"] == w) for w in "AB"}
    assert sent["A"] + sent["B"] == 6 and sent["A"] <= 5 and sent["B"] <= 2


def test_route_city_no_deficit_or_no_headroom_means_no_transfers():
    assert hosp.route_city(toy([("A", "High", 2, 4), ("B", "Low", 1, 3)])) == []
    assert hosp.route_city(toy([("A", "High", 6, 4), ("B", "Low", 3, 3)])) == []


def test_route_city_tie_break_on_utilisation():
    # B and C both have 2 spare beds; C is less loaded (1/3 vs 2/4), so it receives first.
    t = hosp.route_city(toy([("A", "High", 5, 4), ("B", "Low", 2, 4), ("C", "Low", 1, 3)]))
    assert t == [{"from": "A", "to": "C", "patients": 1}]


# ---------------------------------------------------------------- API: the invariants
@pytest.mark.parametrize("params", BUSY)
def test_never_routes_across_cities(client, params):
    d = get(client, **params)
    city_of = {w["ward"]: w["city"] for w in d["wards"]}
    assert d["transfers"], "scenario should produce overflow"
    for t in d["transfers"]:
        assert city_of[t["from"]] == city_of[t["to"]] == t["city"]
        assert t["from"] != t["to"] and t["patients"] > 0


@pytest.mark.parametrize("params", BUSY)
def test_conservation_patients_sent_equal_received(client, params):
    d = get(client, **params)
    w = d["wards"]
    moved = sum(t["patients"] for t in d["transfers"])
    assert sum(x["sent"] for x in w) == sum(x["received"] for x in w) == moved == d["totals"]["transferred"]
    for c in d["cities"]:   # also per city: nothing leaks between cities
        ws = [x for x in w if x["city"] == c["city"]]
        assert sum(x["sent"] for x in ws) == sum(x["received"] for x in ws) == c["transferred"]
    assert sum(x["load_after"] for x in w) == sum(x["demand"] for x in w)   # patients are moved, not lost


@pytest.mark.parametrize("params", BUSY + [dict(capacity_pct=100), dict(capacity_pct=200)])
def test_ward_and_city_maths(client, params):
    d = get(client, **params)
    for w in d["wards"]:
        assert w["deficit"] == max(w["demand"] - w["capacity"], 0)
        assert w["headroom"] == max(w["capacity"] - w["demand"], 0)
        assert w["load_after"] == w["demand"] - w["sent"] + w["received"]
        assert w["net_deficit"] == max(w["load_after"] - w["capacity"], 0)
        assert w["headroom_after"] == max(w["capacity"] - w["load_after"], 0)
        assert w["utilisation"] == pytest.approx(w["demand"] / w["capacity"], abs=1e-4)
        assert w["sent"] <= w["deficit"]                     # only overflow is sent...
        assert w["received"] <= w["headroom"]                # ...only into genuinely free beds
        assert not (w["sent"] and w["received"])
        if w["tier"] == "Critical":
            assert w["received"] == 0
    for c in d["cities"]:
        ws = [x for x in d["wards"] if x["city"] == c["city"]]
        assert c["demand"] == sum(x["demand"] for x in ws) and c["capacity"] == sum(x["capacity"] for x in ws)
        assert c["utilisation"] == pytest.approx(c["demand"] / c["capacity"], abs=1e-4)
        assert c["unresolved"] == sum(x["net_deficit"] for x in ws)
        assert c["remaining_headroom"] == sum(x["headroom_after"] for x in ws)
        assert c["overflow_before"] == c["transferred"] + c["unresolved"]
        assert c["headroom_before"] == c["transferred"] + c["remaining_headroom"]
        # greedy is exhaustive: it only stops when deficits or receivable headroom run out
        assert c["unresolved"] == 0 or c["remaining_headroom"] == c["headroom_reserved"]
    t = d["totals"]
    assert t["utilisation"] == pytest.approx(t["demand"] / t["capacity"], abs=1e-4)


def test_capacity_scale_and_basis(client):
    lo, base, hi = get(client, capacity_pct=50), get(client), get(client, capacity_pct=200)
    assert lo["totals"]["capacity"] < base["totals"]["capacity"] < hi["totals"]["capacity"]
    assert lo["totals"]["unresolved"] >= base["totals"]["unresolved"] >= hi["totals"]["unresolved"]
    for w in base["wards"]:
        assert w["capacity"] == w["baseline_capacity"] and w["demand"] == w["demand_expected"]
    surge = get(client, basis="surge")
    assert all(w["demand"] == w["demand_surge"] >= w["demand_expected"] for w in surge["wards"])


def test_bed_demand_matches_resources_beds_required(client):
    beds = {w["ward"]: w["supplies"]["beds"]["required"]
            for w in client.get("/api/allocation", params={"date": DATE, "h": 3}).json()["wards"]}
    assert {w["ward"]: w["demand"] for w in get(client)["wards"]} == beds


def test_city_scope_equals_its_slice_of_all(client):
    every = get(client, capacity_pct=70)
    for city in CITIES:
        one = get(client, city=city, capacity_pct=70)
        assert {w["city"] for w in one["wards"]} == {city}
        assert one["cities"] == [c for c in every["cities"] if c["city"] == city]
        assert one["transfers"] == [t for t in every["transfers"] if t["city"] == city]
    assert len(every["cities"]) == 5 and every["totals"]["n_wards"] == 60


def test_deterministic(client):
    assert get(client, capacity_pct=65, basis="surge") == get(client, capacity_pct=65, basis="surge")


@pytest.mark.parametrize("params", [
    {"capacity_pct": 49}, {"capacity_pct": 201}, {"capacity_pct": "big"}, {"basis": "worst"},
    {"city": "Atlantis"}, {"h": 1}, {"date": "2025/07/07"},
])
def test_bad_params_are_400(client, params):
    r = client.get("/api/overflow", params=params)
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], str) and "Traceback" not in r.text


def test_no_headroom_means_everything_unresolved(client):
    d = get(client, capacity_pct=50)        # every ward is at or over capacity on the demo week
    assert d["totals"]["headroom_before"] == 0 and d["transfers"] == []
    assert d["totals"]["unresolved"] == d["totals"]["overflow_before"] > 0
