"""Constrained resource allocation: rates, priority, greedy fill, crews, deficit flags, API."""
import math

import numpy as np
import pandas as pd
import pytest

from src import config
from src import resources as res
from src.data import load_raw

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from server.api import app  # noqa: E402

DATE = "2025-07-07"
HIGH = {"High", "Critical"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def get(client, **params):
    r = client.get("/api/allocation", params={"date": DATE, "h": 3, **params})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- rates
@pytest.mark.skipif(not config.ONSITE_CSV.exists(), reason="onsite CSV not present")
def test_rates_fitted_on_training_years_only():
    raw = load_raw(config.ONSITE_CSV)
    rates = res.fit_rates(raw)
    tr = raw[raw.date.dt.year <= config.TRAIN_END_YEAR]
    for s in res.SUPPLIES:
        x, y = tr.dengue_cases.to_numpy(float), tr[s["column"]].to_numpy(float)
        assert rates["supplies"][s["key"]]["k"] == pytest.approx((x * y).sum() / (x * x).sum(), rel=1e-5)
        assert rates["supplies"][s["key"]]["r2_test"] > 0.95
    # learned values land where the data says (no hardcoding: this is a sanity band)
    k = res.rate_k(rates)
    assert 2.3 < k["saline"] < 2.5 and 1.1 < k["kits"] < 1.2 and 0.27 < k["beds"] < 0.33


def test_rates_cache_matches_fit(tmp_path):
    raw = load_raw()
    if "saline_bags_needed" not in raw:
        pytest.skip("onsite CSV not present")
    p = tmp_path / "rates.json"
    a = res.load_rates(raw, path=p)          # fits + writes
    assert p.exists()
    b = res.load_rates(None, path=p)         # reads the cache
    assert a == b


# ---------------------------------------------------------------- pure greedy fill
def test_greedy_fill_priority_order_and_conservation():
    req = np.array([10, 8, 6, 4])
    crit = np.array([True, False, False, False])
    alloc = res.greedy_fill(req, 15, crit)
    assert alloc.tolist() == [10, 5, 0, 0]
    assert alloc.sum() == 15 and (alloc <= req).all()


def test_greedy_fill_floor_after_critical():
    req = np.array([10, 10, 10, 10])
    crit = np.array([True, False, False, False])
    alloc = res.greedy_fill(req, 18, crit, floor_pct=20)
    # Critical fully (10), then 20% floor = 2 each for the other three (6), rest down the list (2)
    assert alloc.tolist() == [10, 4, 2, 2]


def test_greedy_fill_never_overallocates():
    req = np.array([3, 0, 5])
    alloc = res.greedy_fill(req, 1000, np.array([False, True, False]), floor_pct=20)
    assert alloc.tolist() == [3, 0, 5]


def test_crews_down_priority_list():
    assert res.assign_crews(np.array([3, 2, 2, 1]), 6).tolist() == [3, 2, 1, 0]
    assert res.assign_crews(np.array([1, 1]), 0).tolist() == [0, 0]


def test_crews_needed_rule():
    tier = pd.Series(["Low", "Low", "Medium", "High", "Critical"])
    dense = pd.Series([False, True, True, False, True])
    assert res.crews_needed(tier, dense).tolist() == [0, 0, 2, 2, 4]


def test_density_weight_clipped(client):
    p = res.ward_profiles(load_raw())
    assert p.density_weight.between(0.5, 2.0).all()
    assert p.groupby("city").dense_top25.sum().gt(0).all()


# ---------------------------------------------------------------- API: scarcity behaviour
def test_full_stock_has_no_deficits(client):
    d = get(client, stock_pct=100, crews=1000)
    assert d["n_critical_deficit"] == 0 and d["n_warning"] == 0 and d["alerts"] == []
    for w in d["wards"]:
        for s in w["supplies"].values():
            assert s["allocated"] == s["required"] and s["deficit"] == 0
        assert w["crews_assigned"] == w["crews_needed"] and w["flag"] is None
    assert all(t["unmet"] == 0 for t in d["totals"].values())


@pytest.mark.parametrize("stock", [30, 45, 60, 80])
@pytest.mark.parametrize("floor", [0, 20])
def test_conservation(client, stock, floor):
    d = get(client, stock_pct=stock, floor=floor)
    for key, t in d["totals"].items():
        assert t["available"] == math.floor(stock / 100 * t["required"] + 1e-9)
        alloc = sum(w["supplies"][key]["allocated"] for w in d["wards"])
        assert alloc == t["allocated"] <= t["available"]
        assert alloc == min(t["available"], t["required"])           # nothing wasted
        assert all(0 <= w["supplies"][key]["allocated"] <= w["supplies"][key]["required"] for w in d["wards"])


def test_low_stock_serves_critical_first(client):
    d = get(client, stock_pct=30)
    assert any(w["tier"] == "Critical" for w in d["wards"])
    for key in d["totals"]:
        crit = [w for w in d["wards"] if w["tier"] == "Critical"]
        others_got = sum(w["supplies"][key]["allocated"] for w in d["wards"] if w["tier"] != "Critical")
        if others_got:
            assert all(w["supplies"][key]["deficit"] == 0 for w in crit)
        # greedy prefix: once a ward is short, nobody further down gets anything
        seen_short = False
        for w in d["wards"]:
            s = w["supplies"][key]
            if seen_short:
                assert s["allocated"] == 0
            if s["deficit"] > 0:
                seen_short = True


def test_priority_order(client):
    d = get(client, stock_pct=50)
    ranks = [res.TIER_RANK[w["tier"]] for w in d["wards"]]
    assert ranks == sorted(ranks, reverse=True)
    assert [w["priority"] for w in d["wards"]] == list(range(1, len(d["wards"]) + 1))
    for a, b in zip(d["wards"], d["wards"][1:]):
        if a["tier"] == b["tier"]:
            assert a["score"] >= b["score"]
        assert a["score"] == pytest.approx(a["expected"] * a["density_weight"], abs=0.02)


def test_deficit_flags(client):
    d = get(client, stock_pct=40)
    for w in d["wards"]:
        short = any(s["deficit"] > 0 for s in w["supplies"].values())
        want = None if not short else ("CRITICAL DEFICIT" if w["tier"] in HIGH else "WARNING")
        assert w["flag"] == want
    assert d["n_critical_deficit"] == sum(w["flag"] == "CRITICAL DEFICIT" for w in d["wards"]) > 0
    assert len(d["alerts"]) == d["n_critical_deficit"] + d["n_warning"]


def test_floor_gives_every_ward_some_cover_when_stock_allows(client):
    d = get(client, stock_pct=60, floor=20)
    for key, t in d["totals"].items():
        if t["unmet"] == 0:
            continue
        crit_need = sum(w["supplies"][key]["required"] for w in d["wards"] if w["tier"] == "Critical")
        floor_need = sum(math.ceil(0.2 * w["supplies"][key]["required"]) for w in d["wards"] if w["tier"] != "Critical")
        if t["available"] >= crit_need + floor_need:
            assert all(w["supplies"][key]["allocated"] >= math.ceil(0.2 * w["supplies"][key]["required"])
                       for w in d["wards"])


def test_crews_assignment(client):
    d = get(client, crews=15)
    assert d["crews"]["assigned"] == min(15, d["crews"]["needed"])
    assert sum(w["crews_assigned"] for w in d["wards"]) == d["crews"]["assigned"]
    assert all(0 <= w["crews_assigned"] <= w["crews_needed"] for w in d["wards"])
    assert all(w["crews_needed"] == 0 for w in d["wards"] if w["tier"] == "Low")


def test_city_scope(client):
    d = get(client, scope="Gazipur", stock_pct=50)
    assert d["n_wards"] == 5 and {w["city"] for w in d["wards"]} == {"Gazipur"}
    assert get(client, scope="ALL")["n_wards"] == 60


def test_supply_units_follow_rates(client):
    d = get(client)
    k = {s["key"]: s["k"] for s in d["supplies"]}
    for w in d["wards"][:10]:
        assert w["supplies"]["saline"]["required"] == round(k["saline"] * w["expected"]) or \
            abs(w["supplies"]["saline"]["required"] - k["saline"] * w["expected"]) <= 1  # expected is rounded
        assert w["supplies"]["beds"]["safety"] >= w["supplies"]["beds"]["required"]


def test_deterministic(client):
    assert get(client, stock_pct=35, crews=7) == get(client, stock_pct=35, crews=7)


@pytest.mark.parametrize("params", [
    {"stock_pct": 29}, {"stock_pct": 101}, {"stock_pct": "lots"}, {"floor": 10}, {"crews": -1},
    {"scope": "Atlantis"}, {"h": 5}, {"date": "07/07/2025"},
])
def test_bad_params_are_400(client, params):
    r = client.get("/api/allocation", params=params)
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], str) and "Traceback" not in r.text


def test_catch_all_still_404(client):
    assert client.get("/api/allocations").status_code == 404
