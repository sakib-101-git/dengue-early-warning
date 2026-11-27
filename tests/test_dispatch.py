"""Dispatch notices: SMS segment budget, numbers match the engines, plain-language alerts and reasons,
offline brief, determinism, 400s."""
import re
from datetime import datetime

import pytest

from src import dispatch

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from server.api import app  # noqa: E402

DATE = "2025-07-07"
CITIES = ["Chattogram", "Dhaka North", "Dhaka South", "Gazipur", "Narayanganj"]
SCARCE = dict(stock_pct=60, crews=40, basis="surge")          # the UI default scenario: deficits + overflow
WEEKS = ["2024-03-04", "2024-08-05", "2025-07-07", "2025-09-15"]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def notice(client, **params):
    r = client.get("/api/dispatch", params={"date": DATE, "h": 3, **params})
    assert r.status_code == 200, r.text
    return r


def wards(client):
    meta = client.get("/api/meta").json()
    return [w["id"] for c in meta["cities"] for w in c["wards"]]


DECIMAL = re.compile(r"\d\.\d")                                  # cases are whole numbers in human text
JARGON = ("80% range", "q10", "q90", "issue week", "surge demand", "priority #", "bed load", "derived", "headroom",
          "utilisation", "counterfactual", "allocated /", "local bed")


# ---------------------------------------------------------------- plain-language helpers
def test_plain_numbers_and_deadline():
    assert dispatch.cases_phrase(0.4) == "fewer than 1 case"
    assert dispatch.cases_phrase(1.2) == "about 1 case" and dispatch.cases_phrase(8.9) == "about 9 cases"
    assert dispatch.range_phrase(5.2, 13.7) == "likely 5 to 14"
    assert dispatch.range_phrase(0.3, 2.6) == "up to 3" and dispatch.range_phrase(0.1, 0.4) == ""
    # the Monday before the forecast week ...
    assert dispatch.deadline("2025-07-07", "2025-07-28") == "2025-07-21"
    # ... but never sooner than 3 days after the data date
    assert dispatch.deadline("2025-07-07", "2025-07-14") == "2025-07-10"


# ---------------------------------------------------------------- GSM-7 accounting
def test_sms_info_counts_gsm7_and_ucs2_like_a_phone():
    one = dispatch.sms_info("a" * 160)
    assert one["encoding"] == "GSM-7" and one["segments"] == 1 and one["remaining"] == 0
    assert dispatch.sms_info("a" * 161)["segments"] == 2
    assert dispatch.sms_info("a" * 306)["segments"] == 2
    assert dispatch.sms_info("a" * 307)["segments"] == 3
    # extension characters take two septets
    assert dispatch.sms_info("[" * 80)["units"] == 160 and dispatch.sms_info("[" * 81)["segments"] == 2
    # one non-GSM character switches the whole message to UCS-2 (70 / 67)
    u = dispatch.sms_info("a" * 69 + "→")
    assert u["encoding"] == "UCS-2" and u["segments"] == 1
    assert dispatch.sms_info("a" * 70 + "→")["segments"] == 2
    assert dispatch.to_gsm("12–20 → beds × 2") == "12-20 -> beds x 2"


@pytest.mark.parametrize("week", WEEKS)
@pytest.mark.parametrize("stock", [30, 100])
def test_every_ward_sms_fits_two_gsm7_segments(client, week, stock):
    for w in wards(client):
        d = notice(client, scope="ward", id=w, date=week, stock_pct=stock, basis="surge", capacity_pct=50,
                   pool="all", plan="allocation").json()
        info = dispatch.sms_info(d["sms"])
        assert info == d["sms_info"]
        assert info["encoding"] == "GSM-7", d["sms"]
        assert info["segments"] <= 2, d["sms"]
        name, flag = w.replace("_", " "), d["context"]["flag"]
        if flag:   # deficit wards lead with their flag
            assert d["sms"].startswith(f"{dispatch.SMS_FLAG[flag]}: {name} dengue, "), d["sms"]
        else:
            assert d["sms"].startswith((f"Dengue alert {name}: ", f"Dengue update {name}: ")), d["sms"]
        # the signature is the first thing dropped when space is short
        assert d["sms"].endswith(("-Dengue Watch", ".")) and not DECIMAL.search(d["sms"]), d["sms"]


@pytest.mark.parametrize("params", [dict(), dict(pool="all", plan="allocation", **SCARCE)], ids=["api", "ui"])
def test_most_ward_sms_fit_one_segment_at_default_settings(client, params):
    segs = [notice(client, scope="ward", id=w, **params).json()["sms_info"]["segments"] for w in wards(client)]
    assert sum(s == 1 for s in segs) / len(segs) >= 0.8, segs


def test_city_sms_fits_two_gsm7_segments(client):
    for week in WEEKS:
        for c in CITIES:
            d = notice(client, scope="city", id=c, date=week, stock_pct=30, capacity_pct=50, plan="allocation").json()
            assert d["sms_info"]["encoding"] == "GSM-7" and d["sms_info"]["segments"] <= 2, d["sms"]


# ---------------------------------------------------------------- numbers match the engines
@pytest.mark.parametrize("pool", ["city", "all"])
def test_ward_numbers_match_allocation_and_overflow(client, pool):
    for c in CITIES:
        scope = c if pool == "city" else "all"
        alloc = client.get("/api/allocation", params={"scope": scope, "date": DATE, "h": 3, "stock_pct": 60,
                                                      "crews": 40}).json()
        over = client.get("/api/overflow", params={"city": c, "date": DATE, "h": 3, "basis": "surge"}).json()
        a_by = {w["ward"]: w for w in alloc["wards"]}
        o_by = {w["ward"]: w for w in over["wards"]}
        for w in [x["ward"] for x in over["wards"]]:
            ctx = notice(client, scope="ward", id=w, pool=pool, **SCARCE).json()["context"]
            a, o = a_by[w], o_by[w]
            assert (ctx["expected"], ctx["low_80"], ctx["high_80"], ctx["tier"]) == \
                   (a["expected"], a["low_80"], a["high_80"], a["tier"])
            assert ctx["priority"] == a["priority"] and ctx["flag"] == a["flag"]
            assert (ctx["crews_assigned"], ctx["crews_needed"]) == (a["crews_assigned"], a["crews_needed"])
            for s in ctx["supplies"]:
                src = a["supplies"][s["key"]]
                assert (s["required"], s["allocated"], s["deficit"]) == (src["required"], src["allocated"], src["deficit"])
            b = ctx["beds"]
            assert (b["demand"], b["capacity"], b["net_deficit"], b["sent"], b["received"]) == \
                   (o["demand"], o["capacity"], o["net_deficit"], o["sent"], o["received"])
            assert sum(t["patients"] for t in b["transfers_out"]) == o["sent"]
            assert sum(t["patients"] for t in b["transfers_in"]) == o["received"]


def test_city_numbers_match_allocation_and_overflow(client):
    for c in CITIES:
        alloc = client.get("/api/allocation", params={"scope": c, "date": DATE, "h": 3, "stock_pct": 60, "crews": 40}).json()
        over = client.get("/api/overflow", params={"city": c, "date": DATE, "h": 3, "basis": "surge"}).json()
        ctx = notice(client, scope="city", id=c, **SCARCE).json()["context"]
        for s in ctx["supplies"]:
            t = alloc["totals"][s["key"]]
            assert (s["required"], s["allocated"]) == (t["required"], t["allocated"])
        assert ctx["n_critical_deficit"] == alloc["n_critical_deficit"] and ctx["n_warning"] == alloc["n_warning"]
        assert ctx["crews_assigned"] == alloc["crews"]["assigned"] and ctx["crews_needed"] == alloc["crews"]["needed"]
        assert ctx["beds"] == over["cities"][0]
        assert [(t["from"], t["to"], t["patients"]) for t in ctx["transfers"]] == \
               [(t["from"], t["to"], t["patients"]) for t in over["transfers"]]
        assert [w["ward"] for w in ctx["wards"]] == [w["ward"] for w in alloc["wards"]]


def test_crew_impact_matches_simulator(client):
    plan = "DNCC_Ward_01:2,DSCC_Ward_06:3"
    sim = client.get("/api/simulate", params={"date": DATE, "crews": plan}).json()
    s_by = {w["ward"]: w for w in sim["wards"]}
    for w in ("DNCC_Ward_01", "DSCC_Ward_06"):
        d = notice(client, scope="ward", id=w, plan=plan).json()
        assert d["plan"] == "simulator"
        s = d["context"]["sim"]
        assert s["crews"] == s_by[w]["crews"] and s["prevented_cases"] == s_by[w]["prevented"]["cases"]
        assert s["prevented_beds"] == s_by[w]["prevented"]["beds"]
        assert "Spraying should prevent" in d["whatsapp"]
    # plan=allocation simulates exactly the crews Resources assigned
    alloc = client.get("/api/allocation", params={"scope": "all", "date": DATE, "h": 3, "stock_pct": 60, "crews": 40}).json()
    assigned = {w["ward"]: w["crews_assigned"] for w in alloc["wards"] if w["crews_assigned"]}
    d = notice(client, scope="city", id="Dhaka South", pool="all", plan="allocation", **SCARCE).json()
    assert d["plan"] == "allocation"
    assert {w["ward"]: w["sim"]["crews"] for w in d["context"]["wards"] if w["sim"]} == \
           {w: n for w, n in assigned.items() if w.startswith("DSCC")}
    assert notice(client, scope="ward", id="DNCC_Ward_01").json()["context"]["sim"] is None


# ---------------------------------------------------------------- alerts
def test_every_deficit_ward_gets_an_alert_line(client):
    seen = 0
    for w in wards(client):
        d = notice(client, scope="ward", id=w, stock_pct=40, basis="surge", capacity_pct=60, pool="all").json()
        ctx = d["context"]
        short = any(s["deficit"] > 0 for s in ctx["supplies"])
        beds_short = ctx["beds"]["net_deficit"] > 0
        if short:
            seen += 1
            assert ctx["flag"] in ("CRITICAL DEFICIT", "WARNING")
            # the official flag, one plain sentence on what it means, and the reason for the shortage
            meaning = f"{ctx['flag']}: {dispatch.FLAG_MEANING[ctx['flag']]}."
            reason = f"Stock this week covers {ctx['settings']['stock_pct']}% of what"
            assert meaning in d["whatsapp"] and reason in d["whatsapp"]
            assert ctx["flag"] in d["brief"] and reason in d["brief"]
            assert ctx["reasons"]["supplies"].startswith(reason)
            assert any(x in d["sms"] for x in ("Need ", "Short ", "No supplies allocated")), d["sms"]
            assert d["sms"].startswith({"CRITICAL DEFICIT": "CRITICAL: ", "WARNING": "WARNING: "}[ctx["flag"]]), d["sms"]
        else:
            assert ctx["reasons"]["supplies"] is None and "Stock this week covers" not in d["whatsapp"]
            assert not d["sms"].startswith(("CRITICAL:", "WARNING:")), d["sms"]
        if beds_short:
            assert "bed" in d["sms"] and "may have no bed" in d["whatsapp"]
            assert "may have no bed" in ctx["bed_story"]
        assert len(ctx["alerts"]) == int(short) + int(beds_short)
        assert all(a["plain"].startswith(a["label"] + ": ") for a in ctx["alerts"])
    assert seen > 10   # the scenario really is scarce


@pytest.mark.parametrize("scope,ident", [("ward", "DNCC_Ward_01"), ("ward", "DNCC_Ward_09"), ("ward", "CCC_Ward_07"),
                                         ("city", "Dhaka North")])
def test_whatsapp_has_the_required_sections(client, scope, ident):
    d = notice(client, scope=scope, id=ident, pool="all", plan="allocation", **SCARCE).json()
    wa, ctx = d["whatsapp"], d["context"]
    assert wa[0] in "\U0001F534\U0001F7E0\U0001F7E2"            # one status emoji, first
    for head in ("*What to expect*", "*Supplies: need / getting*", "*Hospital beds*", "*Spraying crews*",
                 f"*Please do by {dispatch.day(ctx['deadline'], False)}*"):
        assert head in wa
    assert wa.count("\n1. ") == 1 and 1 <= len(re.findall(r"^\d\. ", wa, flags=re.M)) <= 3
    assert len([x for x in wa.splitlines() if x.strip()]) <= 17
    assert wa.rstrip().endswith(f"from data up to {dispatch.day(ctx['issue_date'], False)}._")
    assert not DECIMAL.search(wa)


# ---------------------------------------------------------------- brief
EXTERNAL = re.compile(r"""(https?:)?//[a-z0-9]|@import|<link\b|<script\b[^>]*\bsrc=""", re.I)


@pytest.mark.parametrize("city", CITIES)
def test_brief_is_self_contained_printable_html(client, city):
    r = notice(client, scope="city", id=city, format="brief", plan="allocation", **SCARCE)
    assert r.headers["content-type"].startswith("text/html")
    html = r.text
    assert html.startswith("<!doctype html>") and html.rstrip().endswith("</html>")
    assert not EXTERNAL.search(html), EXTERNAL.search(html).group(0)
    assert "@page" in html and "size:A4" in html and "@media print" in html and "break-inside:avoid" in html
    for section in ("In short.", "Expected cases", "Supplies covered", "Supplies for the whole city", "Wards needing action",
                    "What to do", "Prepared by", "Approved by", "Forecast for the week of <b>28 Jul 2025</b>",
                    "Numbers are estimates; see page 2 for method.", "How these numbers were made", "Generated ",
                    "from data up to 7 Jul 2025"):
        assert section in html, section
    ctx = notice(client, scope="city", id=city, plan="allocation", **SCARCE).json()["context"]
    rows = re.search(r"Wards needing action</h2>(.*?)</table>", html, flags=re.S)
    assert rows is None or rows.group(1).count("<tr>") - 1 == min(8, len(ctx["urgent"]))   # header row + max 8 wards
    for t in ctx["transfers"]:
        assert f"{t['from_name']} &rarr; {t['to_name']}: {dispatch.plural(t['patients'], 'patient')}" in html


def test_ward_brief_and_text_formats(client):
    r = notice(client, scope="ward", id="DSCC_Ward_06", format="brief")
    assert "DSCC Ward 06, Dhaka South" in r.text and not EXTERNAL.search(r.text)
    sms = notice(client, scope="ward", id="DSCC_Ward_06", format="sms")
    assert sms.headers["content-type"].startswith("text/plain") and sms.text.startswith("Dengue alert DSCC Ward 06:")
    wa = notice(client, scope="ward", id="DSCC_Ward_06", format="whatsapp")
    assert wa.text[2:].startswith("*Dengue alert: DSCC Ward 06, Dhaka South*")


def test_dncc_ward_01_reference_texts(client):
    d = notice(client, scope="ward", id="DNCC_Ward_01", pool="all", plan="allocation", **SCARCE).json()
    assert d["sms"] == ("CRITICAL: DNCC Ward 01 dengue, about 9 cases wk of 28 Jul (up to 14). Need 21 saline, 10 kits, "
                        "3 beds, none allocated yet. Resupply by 21 Jul. -Dengue Watch")
    wa = d["whatsapp"]
    # actions carry their amounts
    assert "1. Request 21 saline bags, 10 test kits and 3 extra beds from the regional store." in wa
    assert "Ask the city for 2 spraying crews." in wa
    # the two kinds of bed are always labelled
    assert "extra (surge) beds 3 / 0" in wa
    assert ("Up to 4 patients may need a bed in a bad week, which fills all 4 regular beds; other Dhaka North wards "
            "have none free, so the ward needs extra beds.") in wa


def test_bed_numbers_are_always_labelled(client):
    for w in wards(client):
        ctx = notice(client, scope="ward", id=w, pool="all", plan="allocation", **SCARCE).json()["context"]
        assert "regular bed" in ctx["bed_story"], ctx["bed_story"]
        for a in ctx["actions"]:
            if "bed" in a["text"] and a["key"] != "transfer_out":
                assert "extra bed" in a["text"] or "regular bed" in a["text"], a["text"]


def test_method_page_defaults_by_scope_and_can_be_switched(client):
    page2 = "How these numbers were made"
    ward = notice(client, scope="ward", id="DNCC_Ward_01").json()
    city = notice(client, scope="city", id="Dhaka North").json()
    assert ward["method"] is False and page2 not in ward["brief"] and "see page 2" not in ward["brief"]
    assert city["method"] is True and page2 in city["brief"]
    assert page2 in notice(client, scope="ward", id="DNCC_Ward_01", method=1).json()["brief"]
    assert page2 not in notice(client, scope="city", id="Dhaka North", method=0).json()["brief"]
    # the JSON context is the same either way: only the printed brief changes
    assert notice(client, scope="ward", id="DNCC_Ward_01", method=1).json()["context"] == ward["context"]


def test_brief_describes_the_model_inputs_correctly(client):
    """The deployed model uses weather, ward conditions and season only (no case counts as input)."""
    foot = "Forecast from weather and ward conditions, using a model trained on 2017–2023 case records."
    for kw in (dict(scope="city", id="Dhaka North"), dict(scope="ward", id="DNCC_Ward_01"),
               dict(scope="ward", id="DNCC_Ward_01", method=1)):
        brief = notice(client, **kw).json()["brief"]
        assert foot + " Numbers are estimates" in brief and "past case data" not in brief
    method = notice(client, scope="city", id="Dhaka North").json()["brief"]
    assert "trained on 2017 to 2023 case records" in method and "does not use recent\ncase counts" in method


def test_breeding_site_checks_start_this_week(client):
    seen = 0
    for w in wards(client)[:30]:
        ctx = notice(client, scope="ward", id=w, **SCARCE).json()["context"]
        by = dispatch.day(ctx["deadline"], False)
        for a in ctx["actions"]:
            if "breeding sites" in a["text"]:
                seen += 1
                assert a["text"].endswith("this week.") and f"by {by}" not in a["text"], a["text"]
                assert a["sms"] == "Start door-to-door checks this week."
            elif a["key"] in ("top_up", "crews", "transfer_out"):
                assert a["text"].endswith(f"by {by}."), a["text"]           # other actions keep their deadline
    assert seen


@pytest.mark.parametrize("ident", ["DNCC_Ward_01", "DSCC_Ward_06", "CCC_Ward_07", "GCC_Ward_02", "NCC_Ward_05"])
def test_human_text_has_no_internal_terms(client, ident):
    d = notice(client, scope="ward", id=ident, pool="all", plan="allocation", method=1, **SCARCE).json()
    page1 = d["brief"].split("How these numbers were made")[0]
    for text in (d["sms"], d["whatsapp"], d["brief"]):
        low = text.lower()
        assert not [j for j in JARGON if j in low], [j for j in JARGON if j in low]
    body = re.sub(r"<style>.*?</style>", "", page1, flags=re.S)
    assert not DECIMAL.search(re.sub(r"<[^>]+>", " ", body))
    assert d["context"]["summary"] in d["brief"].replace("&#x27;", "'")
    # at most 3 actions; the time-bound ones carry the deadline
    by = dispatch.day(d["context"]["deadline"], False)
    acts = d["context"]["actions"][:3]
    assert 1 <= len(acts) <= 3 and all(a["text"].endswith(f"by {by}.") or a["key"] == "tier" for a in acts)


# ---------------------------------------------------------------- determinism
def test_output_is_deterministic(client):
    fixed = datetime(2025, 7, 7, 9, 0)
    a = notice(client, scope="city", id="Dhaka North", plan="allocation", **SCARCE).json()
    alloc = client.get("/api/allocation", params={"scope": "Dhaka North", "date": DATE, "h": 3, "stock_pct": 60, "crews": 40}).json()
    over = client.get("/api/overflow", params={"city": "Dhaka North", "date": DATE, "h": 3, "basis": "surge"}).json()
    x = dispatch.build("city", "Dhaka North", alloc, over, None, generated_at=fixed)
    y = dispatch.build("city", "Dhaka North", alloc, over, None, generated_at=fixed)
    assert x == y
    assert "Generated 2025-07-07 09:00" in x["brief"]
    # the API's SMS / WhatsApp match a fresh build from the same engine payloads (no hidden state)
    b = notice(client, scope="city", id="Dhaka North", **SCARCE).json()
    assert b["sms"] == x["sms"] and b["whatsapp"] == x["whatsapp"]
    assert a["sms"] != "" and a == notice(client, scope="city", id="Dhaka North", plan="allocation", **SCARCE).json()


def test_safe_file_names():
    assert dispatch.safe_name("Dhaka North") == "Dhaka_North"
    assert dispatch.safe_name('a:b/c\\d*e?"f<g>|') == "a_b_c_d_e_f_g"
    assert dispatch.safe_name("DNCC_Ward_01") == "DNCC_Ward_01"


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("params,msg", [
    (dict(scope="state"), "scope"),
    (dict(format="pdf"), "format"),
    (dict(scope="ward", id="Nowhere_Ward_1"), "Unknown ward"),
    (dict(scope="city", id="Atlantis"), "Unknown city"),
    (dict(h=7), "Weeks ahead"),
    (dict(stock_pct=10), "stock_pct"),
    (dict(crews=-1), "crews"),
    (dict(floor=5), "floor"),
    (dict(capacity_pct=300), "capacity_pct"),
    (dict(basis="worst"), "basis"),
    (dict(pool="region"), "pool"),
    (dict(effect=50), "effect"),
    (dict(plan="DNCC_Ward_01:x"), "whole number"),
    (dict(plan="Nowhere:2"), "Unknown ward"),
    (dict(date="07/07/2025"), "YYYY-MM-DD"),
    (dict(method=2), "method"),
])
def test_bad_params_are_400(client, params, msg):
    r = client.get("/api/dispatch", params={"scope": "ward", "id": "DNCC_Ward_01", **params})
    assert r.status_code == 400, r.text
    assert msg in r.json()["detail"]


def test_dates_are_clamped(client):
    lo = notice(client, scope="ward", id="DNCC_Ward_01", date="2000-01-01").json()
    hi = notice(client, scope="ward", id="DNCC_Ward_01", date="2099-01-01").json()
    assert lo["context"]["issue_date"] < hi["context"]["issue_date"]
