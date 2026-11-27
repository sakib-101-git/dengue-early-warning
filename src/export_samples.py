"""Write sample dispatch notices and plans to exports/ (fully offline, deterministic templates).

    python -m src.export_samples                 # demo week: data up to 2025-07-07, 3 weeks ahead

Uses the same cached payload builders as the API (server.api), so every exported number matches
/api/allocation, /api/overflow, /api/simulate and /api/dispatch for the same settings:
    exports/sms/<ward>.txt, exports/sms/city_<city>.txt
    exports/whatsapp/<ward>.txt, exports/whatsapp/city_<city>.txt
    exports/briefs/<city>.html (city brief + method page) and briefs/<ward>.html (one-page ward brief)
    exports/allocation_plan.csv, exports/overflow_routes.csv
"""
from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from src.config import ROOT

EXPORTS = ROOT / "exports"
DEMO = {"date": "2025-07-07", "h": 3, "stock_pct": 60, "crews": 40, "floor": 0, "capacity_pct": 100,
        "basis": "surge", "pool": "all", "plan": "allocation", "effect": 10}
TOP_PER_CITY = 3


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text if text.endswith("\n") else text + "\n")


def main(out: Path = EXPORTS) -> list[Path]:
    from server import api
    from src import dispatch
    from src import resources as res

    if api.STATE is None:
        api.STATE = api.State()
    s = api.S()
    p = DEMO
    issue = s.snap(p["h"], pd.Timestamp(p["date"]).date())
    sim_issue = api.snap_sim(pd.Timestamp(p["date"]).date())

    def notice(scope: str, ident: str) -> dict:
        return api._dispatch_payload(scope, ident, issue, p["h"], p["stock_pct"], p["crews"], p["floor"],
                                     p["capacity_pct"], p["basis"], p["pool"], p["plan"], p["effect"], sim_issue)

    written: list[Path] = []
    alloc = api._allocation_payload("all", issue, p["h"], p["stock_pct"], p["crews"], p["floor"])
    for city in s.cities:
        ranked = [w for w in alloc["wards"] if w["city"] == city]
        top = [w["ward"] for w in ranked[:TOP_PER_CITY]]
        short = next((w["ward"] for w in ranked if w["flag"] and w["ward"] not in top), None)
        top += [short] if short else []          # plus the first ward with a supply deficit, if any
        for w in top:
            d = notice("ward", w)
            for kind, folder, ext in (("sms", "sms", "txt"), ("whatsapp", "whatsapp", "txt"), ("brief", "briefs", "html")):
                path = out / folder / f"{dispatch.safe_name(w)}.{ext}"
                _write(path, d[kind])
                written.append(path)
        d = notice("city", city)
        stem = dispatch.safe_name(city)
        for kind, folder, name in (("sms", "sms", f"city_{stem}.txt"), ("whatsapp", "whatsapp", f"city_{stem}.txt"),
                                   ("brief", "briefs", f"{stem}.html")):
            path = out / folder / name
            _write(path, d[kind])
            written.append(path)

    # allocation plan: every ward, in priority order, with supplies, crews and the bed position
    over = api._overflow_payload("all", issue, p["h"], p["capacity_pct"], p["basis"])
    beds = {w["ward"]: w for w in over["wards"]}
    path = out / "allocation_plan.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["priority", "ward_id", "city", "tier", "expected_cases", "low_80", "high_80",
                     *[f"{k}_{x}" for k in res.SUPPLY_KEYS for x in ("required", "allocated", "deficit", "coverage")],
                     "crews_needed", "crews_assigned", "flag", "bed_demand", "bed_capacity", "beds_sent",
                     "beds_received", "beds_net_deficit", "issue_date", "target_date"])
        for w in alloc["wards"]:
            b = beds[w["ward"]]
            cw.writerow([w["priority"], w["ward"], w["city"], w["tier"], w["expected"], w["low_80"], w["high_80"],
                         *[w["supplies"][k][x] for k in res.SUPPLY_KEYS for x in ("required", "allocated", "deficit", "coverage")],
                         w["crews_needed"], w["crews_assigned"], w["flag"] or "", b["demand"], b["capacity"],
                         b["sent"], b["received"], b["net_deficit"], alloc["issue_date"], alloc["target_date"]])
    written.append(path)

    path = out / "overflow_routes.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["order", "city", "from_ward", "to_ward", "patients", "issue_date", "target_date", "basis", "capacity_pct"])
        for i, t in enumerate(over["transfers"], 1):
            cw.writerow([i, t["city"], t["from"], t["to"], t["patients"], over["issue_date"], over["target_date"],
                         over["basis"], over["capacity_pct"]])
        if not over["transfers"]:   # keep the file meaningful even when nothing needs routing
            cw.writerow(["", "", "", "", 0, over["issue_date"], over["target_date"], over["basis"], over["capacity_pct"]])
    written.append(path)

    settings = ", ".join(f"{k}={v}" for k, v in p.items())
    _write(out / "README.md", "\n".join([
        "# Sample dispatch exports",
        "",
        "Generated offline by `python -m src.export_samples` from the same engines as the API.",
        f"Scenario: {settings}.",
        f"Issue week (data up to) {alloc['issue_date']}, forecast week {alloc['target_date']}.",
        "",
        f"- `sms/`, `whatsapp/`: notices for the top {TOP_PER_CITY} priority wards of each city and its first "
        "ward with a supply deficit, plus a `city_<city>.txt` summary. SMS are GSM-7, 1 segment where "
        "possible and never more than 2.",
        "- `briefs/`: printable A4 briefs: one per city (with the plain-language method page) and a one-page "
        "brief per exported ward. Open in a browser and use Print, then Save as PDF.",
        "- `allocation_plan.csv`: every ward in priority order: supplies required/allocated, crews, flags, beds.",
        "- `overflow_routes.csv`: same-city bed transfers (from, to, patients).",
    ]))
    written.append(out / "README.md")
    return written


if __name__ == "__main__":  # pragma: no cover
    files = main()
    print(f"wrote {len(files)} files to {EXPORTS}")
