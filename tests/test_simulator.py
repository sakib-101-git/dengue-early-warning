"""What-if vector control simulator: effect formula, rebuild equivalence, monotonicity, ROI ranking, API."""
import numpy as np
import pandas as pd
import pytest

from src import simulator as sim
from src.config import HORIZONS

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from server.api import app  # noqa: E402

DATE = "2025-07-07"
ISSUE = pd.Timestamp(DATE)
WARDS = ["DNCC_Ward_09", "DSCC_Ward_06", "CCC_Ward_02", "GCC_Ward_03", "NCC_Ward_01"]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def engine():
    import server.api as api
    with TestClient(app):
        return api.STATE.engine


def sim_get(client, **params):
    r = client.get("/api/simulate", params={"date": DATE, **params})
    assert r.status_code == 200, r.text
    return r.json()


def crews_text(plan: dict) -> str:
    return ",".join(f"{w}:{n}" for w, n in plan.items())


# ---------------------------------------------------------------- effect formula
def test_reduction_compounds_and_caps():
    assert sim.reduction(0) == 0
    assert sim.reduction(1) == pytest.approx(0.10)
    assert sim.reduction(3) == pytest.approx(1 - 0.9 ** 3)
    assert sim.reduction(7) == pytest.approx(0.50) and sim.reduction(20) == pytest.approx(0.50)
    assert sim.reduction(2, effect_pct=20) == pytest.approx(0.36)
    assert np.all(np.diff(sim.reduction(np.arange(25), 5)) >= 0)


@pytest.mark.parametrize("effect,useful", [(5, 14), (10, 7), (15, 5), (20, 4)])
def test_useful_crews_is_where_cap_is_reached(effect, useful):
    assert sim.useful_crews(effect) == useful
    assert sim.reduction(useful, effect) == pytest.approx(sim.CAP)
    assert sim.reduction(useful - 1, effect) < sim.CAP


# ---------------------------------------------------------------- the model path
@pytest.mark.parametrize("ward", WARDS[:3])
@pytest.mark.parametrize("h", HORIZONS)
def test_feature_scaling_equals_full_rebuild(engine, ward, h):
    """Scaling the two index features == rebuilding the ward's features from modified raw rows."""
    b = engine.bundles[h]
    pct = 100 * float(sim.reduction(3))
    rebuilt = engine.what_if_frame(ward, ISSUE, h, waterlog_pct=pct)
    rebuilt = rebuilt[rebuilt.date == ISSUE][b["features"]].to_numpy(float)
    rows = engine.feature_rows(ISSUE, h)
    fast = sim.apply_reduction(rows[rows.ward_id == ward][b["features"]], sim.reduction(3)).to_numpy(float)
    np.testing.assert_allclose(fast, rebuilt, rtol=0, atol=1e-12)


def test_zero_crews_is_the_baseline_forecast(engine):
    cur = sim.response_curves(engine, ISSUE)
    for h in HORIZONS:
        base = cur[(cur.h == h) & (cur.crews == 0)].set_index("ward_id")
        t = engine.forecast_table(h)
        t = t[t.issue_date == ISSUE].set_index("ward_id").loc[base.index]
        np.testing.assert_allclose(base["expected"], t["expected_cases"], rtol=1e-9)
        np.testing.assert_allclose(base["high_80"], t["high_80"], rtol=1e-9)
        assert (base["tier"] == t["tier"]).all()


@pytest.mark.parametrize("date", ["2024-08-05", DATE, "2025-10-13"])
@pytest.mark.parametrize("effect", [5, 10, 20])
def test_more_crews_never_increase_cases(engine, date, effect):
    cur = sim.response_curves(engine, pd.Timestamp(date), effect).sort_values(["ward_id", "h", "crews"])
    for col in ("expected", "low_80", "high_80"):
        step = cur.groupby(["ward_id", "h"])[col].diff().dropna()
        assert (step <= 1e-12).all(), col
    assert (cur["low_80"] <= cur["expected"] + 1e-12).all() and (cur["high_80"] >= cur["expected"] - 1e-12).all()
    assert (cur["expected"] <= cur["model_expected"] + 1e-12).all()     # the guard only ever lowers


def test_crews_actually_reduce_cases(engine):
    """Sanity: the cap (50% index cut) prevents a real share of cases across all wards."""
    cv = sim.Curves(sim.response_curves(engine, ISSUE), 10)
    base = cv.cube["expected"][:, :, 0].sum()
    assert cv.prevented[:, -1].sum() / base > 0.10
    assert (cv.prevented[:, -1] > 0).mean() > 0.5


# ---------------------------------------------------------------- API: simulate
def test_no_crews_means_no_impact(client):
    for params in ({}, {"crews": ""}, {"crews": '{"DNCC_Ward_09": 0}'}):
        d = sim_get(client, **params)
        assert d["wards"] == [] and d["crews_deployed"] == 0
        assert d["totals"]["prevented"] == {"cases": 0.0, "beds": 0, "saline": 0}


def test_baseline_matches_ward_forecast(client):
    d = sim_get(client, crews="DNCC_Ward_09:2")
    w = d["wards"][0]
    for x in w["horizons"]:
        fc = client.get("/api/ward", params={"ward": "DNCC_Ward_09", "date": DATE, "h": x["h"]}).json()["forecast"]
        assert x["baseline"]["cases"] == fc["expected"] and x["baseline"]["tier"] == fc["tier"]
        assert x["target_date"] == fc["target_date"]


@pytest.mark.parametrize("ward", WARDS)
def test_api_more_crews_never_increase_cases(client, ward):
    prev_cases, prev_total = None, -1.0
    for n in range(0, 12):
        d = sim_get(client, crews=f"{ward}:{n}")
        if n == 0:
            assert d["wards"] == []
            continue
        w = d["wards"][0]
        cases = [x["scenario"]["cases"] for x in w["horizons"]]
        for x in w["horizons"]:
            assert x["scenario"]["cases"] <= x["baseline"]["cases"]
            assert x["scenario"]["beds"] <= x["baseline"]["beds"] and x["scenario"]["saline"] <= x["baseline"]["saline"]
        if prev_cases:
            assert all(c <= p for c, p in zip(cases, prev_cases))
        assert w["prevented"]["cases"] >= prev_total
        assert w["roi"] == pytest.approx(w["prevented"]["cases"] / n, abs=1e-3)
        prev_cases, prev_total = cases, w["prevented"]["cases"]


def test_supply_units_follow_rates(client):
    d = sim_get(client, crews="DSCC_Ward_06:3,GCC_Ward_03:2")
    k = d["rates"]
    for w in d["wards"]:
        for x in w["horizons"]:
            for side in ("baseline", "scenario"):
                p = x[side]
                for s in ("beds", "saline"):        # round(k x cases); API cases are rounded to 0.01
                    assert isinstance(p[s], int) and abs(p[s] - k[s] * p["cases"]) <= 0.5 + k[s] * 0.005
            assert x["prevented"]["beds"] == x["baseline"]["beds"] - x["scenario"]["beds"]


def test_totals_add_up(client):
    d = sim_get(client, crews="DSCC_Ward_06:3,GCC_Ward_03:2,CCC_Ward_02:1")
    assert d["crews_deployed"] == 6 and d["wards_deployed"] == 3
    t = d["totals"]
    assert t["prevented"]["cases"] == pytest.approx(sum(w["prevented"]["cases"] for w in d["wards"]), abs=1e-2)
    assert t["prevented"]["saline"] == sum(w["prevented"]["saline"] for w in d["wards"])
    assert t["prevented"]["cases"] == pytest.approx(sum(r["prevented_cases"] for r in t["by_horizon"]), abs=1e-2)
    assert t["roi"] == pytest.approx(t["prevented"]["cases"] / 6, abs=1e-3)
    assert [r["h"] for r in t["by_horizon"]] == list(HORIZONS)
    cases = [w["prevented"]["cases"] for w in d["wards"]]
    assert cases == sorted(cases, reverse=True)


def test_crews_beyond_cap_add_nothing(client):
    a = sim_get(client, crews="DSCC_Ward_06:7")["wards"][0]
    b = sim_get(client, crews="DSCC_Ward_06:15")["wards"][0]
    assert a["reduction"] == b["reduction"] == 0.5
    assert a["prevented"] == b["prevented"] and b["effective_crews"] == 7


def test_json_and_post_match_compact_get(client):
    a = sim_get(client, crews="DSCC_Ward_06:3,GCC_Ward_03:2")
    b = sim_get(client, crews='{"GCC_Ward_03": 2, "DSCC_Ward_06": 3}')
    r = client.post("/api/simulate", json={"date": DATE, "crews": {"DSCC_Ward_06": 3, "GCC_Ward_03": 2}})
    assert r.status_code == 200, r.text
    assert a == b == r.json()


def test_dates_snap_and_clamp(client):
    assert sim_get(client, date="2025-07-09")["issue_date"] == DATE
    lo = client.get("/api/simulate", params={"date": "2000-01-01"}).json()["issue_date"]
    hi = client.get("/api/simulate", params={"date": "2099-01-01"}).json()["issue_date"]
    assert lo < DATE < hi


# ---------------------------------------------------------------- API: ranking + auto-deploy
def test_rank_sorted_by_one_crew_roi(client):
    r = client.get("/api/simulate/rank", params={"date": DATE})
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["wards"]) == 60 and [w["rank"] for w in d["wards"]] == list(range(1, 61))
    roi = [w["roi_1"] for w in d["wards"]]
    assert roi == sorted(roi, reverse=True)
    assert all(w["prevented_max"] >= w["roi_1"] >= 0 for w in d["wards"])
    assert all(w["best_roi"] >= w["roi_1"] for w in d["wards"])
    top = d["wards"][0]
    one = sim_get(client, crews=f"{top['ward']}:1")["wards"][0]
    assert one["prevented"]["cases"] == pytest.approx(top["roi_1"], abs=1e-2)


def test_rank_city_scope(client):
    d = client.get("/api/simulate/rank", params={"date": DATE, "scope": "Gazipur"}).json()
    assert len(d["wards"]) == 5 and {w["city"] for w in d["wards"]} == {"Gazipur"}


@pytest.mark.parametrize("n", [0, 1, 5, 20])
def test_auto_deploy_respects_budget(client, n):
    d = sim_get(client, auto=n)
    assert d["mode"] == "auto" and d["crews_deployed"] <= n and d["crews_spare"] == n - d["crews_deployed"]
    assert sum(d["crews"].values()) == d["crews_deployed"]
    assert all(c <= d["max_useful_crews"] for c in d["crews"].values())
    if n:
        assert d["crews_deployed"] > 0 and d["totals"]["prevented"]["cases"] > 0


def test_auto_deploy_stays_in_scope(client):
    d = sim_get(client, auto=8, scope="Dhaka South")
    assert d["crews"] and all(w.startswith("DSCC_") for w in d["crews"])


def test_auto_deploy_beats_naive_plans(client):
    """Greedy marginal ROI prevents at least as much as 1 crew to each top-N ward, or all N to the top ward."""
    n = 10
    auto = sim_get(client, auto=n)["totals"]["prevented"]["cases"]
    rank = client.get("/api/simulate/rank", params={"date": DATE}).json()["wards"]
    spread = sim_get(client, crews=crews_text({w["ward"]: 1 for w in rank[:n]}))["totals"]["prevented"]["cases"]
    stack = sim_get(client, crews=f"{rank[0]['ward']}:{n}")["totals"]["prevented"]["cases"]
    assert auto >= spread - 1e-6 and auto >= stack - 1e-6


def test_auto_deploy_marginal_gains_never_wasted(client):
    """Re-simulating the auto plan as a manual plan gives the same numbers (deterministic)."""
    a = sim_get(client, auto=12)
    m = sim_get(client, crews=crews_text(a["crews"]))
    assert a["totals"] == m["totals"] and a["wards"] == m["wards"]
    assert sim_get(client, auto=12) == a


def test_effect_changes_impact(client):
    lo = sim_get(client, crews="DSCC_Ward_06:3", effect=5)["wards"][0]
    hi = sim_get(client, crews="DSCC_Ward_06:3", effect=20)["wards"][0]
    assert lo["reduction"] < hi["reduction"] and lo["prevented"]["cases"] <= hi["prevented"]["cases"]


@pytest.mark.parametrize("params", [
    {"effect": 4}, {"effect": 21}, {"crews": "{bad json"}, {"crews": "Nowhere_Ward:2"}, {"crews": "DNCC_Ward_01"},
    {"crews": "DNCC_Ward_01:-1"}, {"crews": "DNCC_Ward_01:21"}, {"crews": "DNCC_Ward_01:two"},
    {"crews": "DNCC_Ward_01:1.5"}, {"crews": "[1, 2]"}, {"auto": -1}, {"auto": 5000}, {"scope": "Atlantis"},
    {"date": "07-07-2025"},
])
def test_bad_params_are_400(client, params):
    r = client.get("/api/simulate", params=params)
    assert r.status_code == 400, r.text
    assert isinstance(r.json()["detail"], str) and r.json()["detail"]


def test_bad_post_and_rank_are_400(client):
    assert client.post("/api/simulate", json={"crews": {"DNCC_Ward_01": True}}).status_code == 400
    assert client.post("/api/simulate", json={"effect": "lots"}).status_code == 400
    assert client.post("/api/simulate", json={"date": "soon"}).status_code == 400
    assert client.get("/api/simulate/rank", params={"effect": 50}).status_code == 400
    assert client.get("/api/simulate/rank", params={"scope": "Atlantis"}).status_code == 400
