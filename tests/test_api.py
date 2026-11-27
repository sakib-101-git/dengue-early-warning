"""End-to-end checks for the FastAPI layer that feeds the web UI.

Run:  python -m pytest -q tests/test_api.py
"""
import pytest

pytest.importorskip("httpx")  # FastAPI's TestClient needs httpx (requirements.txt)
from fastapi.testclient import TestClient  # noqa: E402

from server.api import app  # noqa: E402

CITIES = ["Chattogram", "Dhaka North", "Dhaka South", "Gazipur", "Narayanganj"]
TIERS = {"Low", "Medium", "High", "Critical"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # runs the lifespan: engine + tables built once
        yield c


@pytest.fixture(scope="module")
def meta(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    return r.json()


def first_ward(meta, city):
    return next(c for c in meta["cities"] if c["name"] == city)["wards"][0]["id"]


# ---------------------------------------------------------------- basics
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_meta_shape(meta):
    assert [c["name"] for c in meta["cities"]] == CITIES
    assert sum(len(c["wards"]) for c in meta["cities"]) == 60
    assert meta["horizons"] == [2, 3, 4]
    assert set(meta["tier_colors"]) == TIERS
    assert meta["min_date"] < meta["default"]["date"] <= meta["max_date"]
    w = meta["cities"][0]["wards"][0]
    assert w["label"].startswith("Ward ")


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Dengue Watch" in r.text
    assert client.get("/vendor/echarts.min.js").status_code == 200


# ---------------------------------------------------------------- ward
@pytest.mark.parametrize("city", CITIES)
@pytest.mark.parametrize("h", [2, 3, 4])
def test_ward_every_city_and_horizon(client, meta, city, h):
    ward = first_ward(meta, city)
    r = client.get("/api/ward", params={"ward": ward, "date": "2024-08-05", "h": h})
    assert r.status_code == 200, r.text
    d = r.json()
    f = d["forecast"]
    assert f["city"] == city and f["horizon"] == h and f["tier"] in TIERS
    assert 0 <= f["low_80"] <= f["expected"] <= f["high_80"]
    assert len(d["rain"]) == 12 and len(d["ahead"]) == h - 1
    # breeding window = rain 2-4 weeks before the target week
    assert all(x["breeding"] == (2 <= x["weeks_before_target"] <= 4) for x in d["rain"] + d["ahead"])
    assert d["season"] and all(s["date"][:4] == f["target_date"][:4] for s in d["season"])
    assert d["drivers"] and all("label" in x and "pct_effect" in x for x in d["drivers"])
    assert d["simulated"] is False and d["forecast"] == d["baseline"]


@pytest.mark.parametrize("date,h", [("2016-02-29", 2), ("1990-01-01", 3), ("2019-06-15", 3), ("2025-12-29", 4), ("2031-01-01", 2)])
def test_ward_dates_snap_and_clamp(client, meta, date, h):
    r = client.get("/api/ward", params={"ward": "DNCC_Ward_01", "date": date, "h": h})
    assert r.status_code == 200, r.text
    issue = r.json()["forecast"]["issue_date"]
    assert meta["min_date"] <= issue <= meta["max_date"]
    if meta["min_date"] <= date <= meta["max_date"]:
        assert issue <= date  # snapped to the latest available week on or before the date


def test_future_week_has_no_actual(client):
    d = client.get("/api/ward", params={"ward": "DNCC_Ward_01", "date": "2025-12-29", "h": 4}).json()
    assert d["forecast"]["actual"] is None


@pytest.mark.parametrize("rain,temp,hum", [(-80, 0, 0), (150, 0, 0), (0, -4, 0), (0, 4, 0), (0, 0, -20), (0, 0, 20), (150, 4, 20), (-80, -4, -20)])
def test_simulation_extremes(client, rain, temp, hum):
    r = client.get("/api/ward", params={"ward": "DNCC_Ward_05", "date": "2025-07-07", "h": 3,
                                        "rain": rain, "temp": temp, "hum": hum})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["simulated"] is True and d["forecast"]["expected"] >= 0
    assert d["baseline"]["expected"] >= 0


def test_more_rain_raises_risk(client):
    p = {"ward": "DNCC_Ward_01", "date": "2025-07-07", "h": 3}
    base = client.get("/api/ward", params=p).json()["forecast"]["expected"]
    wet = client.get("/api/ward", params={**p, "rain": 150}).json()
    assert wet["forecast"]["expected"] > base
    # simulated rain shows up in the rain bars of the last 4 weeks
    assert wet["rain"][-1]["mm"] > client.get("/api/ward", params=p).json()["rain"][-1]["mm"]


@pytest.mark.parametrize("params", [
    {"ward": "NOT_A_WARD"},
    {"ward": "DNCC_Ward_01", "h": 5},
    {"ward": "DNCC_Ward_01", "date": "07/07/2025"},
    {"ward": "DNCC_Ward_01", "rain": 500},
    {},
])
def test_ward_bad_params_are_400(client, params):
    r = client.get("/api/ward", params=params)
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], str) and "Traceback" not in r.text


# ---------------------------------------------------------------- city + heatmap
@pytest.mark.parametrize("city", CITIES)
@pytest.mark.parametrize("h", [2, 3, 4])
def test_city_ranking(client, meta, city, h):
    d = client.get("/api/city", params={"city": city, "date": "2025-07-07", "h": h}).json()
    n = len(next(c for c in meta["cities"] if c["name"] == city)["wards"])
    assert d["n_wards"] == n and len(d["wards"]) == n
    exp = [w["expected"] for w in d["wards"]]
    assert exp == sorted(exp, reverse=True)
    assert sum(d["tier_counts"].values()) == n
    assert d["need_action"] == d["tier_counts"]["High"] + d["tier_counts"]["Critical"]


def test_city_unknown_is_400(client):
    assert client.get("/api/city", params={"city": "Atlantis"}).status_code == 400


@pytest.mark.parametrize("city", CITIES)
def test_heatmap(client, city):
    d = client.get("/api/heatmap", params={"city": city, "year": 2025, "h": 3}).json()
    assert len(d["weeks"]) >= 50
    assert all(0 <= c[0] < len(d["weeks"]) and 0 <= c[1] < len(d["wards"]) and 0 <= c[2] <= 3 for c in d["cells"])


def test_heatmap_bad_year_is_400(client):
    assert client.get("/api/heatmap", params={"city": "Gazipur", "year": 2005, "h": 3}).status_code == 400


# ---------------------------------------------------------------- model
@pytest.mark.parametrize("h", [2, 3, 4])
def test_model(client, h):
    d = client.get("/api/model", params={"h": h}).json()
    assert 0 < d["test"]["MAE"] < 5 and d["gain_vs_same_week"] > 1
    assert sum(c["ours"] for c in d["comparison"]) == 1
    assert set(d["backtest"]) == {"2", "3", "4"}
    assert d["thresholds"] == sorted(d["thresholds"])


def test_unknown_api_route_is_404(client):
    assert client.get("/api/nope").status_code == 404
