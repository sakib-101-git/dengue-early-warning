"""Multi-channel dispatch: plain-language SMS, WhatsApp and printable briefs for ward and city officers.

Nothing here computes a forecast, an allocation or a route. Every number is read from the
payloads the existing engines already serve (GET /api/allocation, /api/overflow and, when a crew
plan is given, /api/simulate), so a notice always matches what the Resources, Hospitals and
Simulator views show for the same settings. Templates are plain Python strings: no LLM, no
network, the same input always gives the same text (the brief's "generated" stamp is the only
clock-dependent line, and it can be fixed by the caller).

Writing rules (the notices are read by councillors and health officers, not analysts)
- Plain English, short sentences, no internal terms. Cases are whole numbers ("about 9 cases,
  likely 5 to 14"; "fewer than 1" below 1). The official flags CRITICAL DEFICIT / WARNING are kept
  and always followed by one plain sentence and the reason for the shortage.
- Two kinds of bed, always labelled: "extra (surge) beds" are the bed supply from the allocation (need /
  getting, like saline and kits); "regular beds" are a ward's usual capacity from overflow routing.
  One bed story per ward: patients who may need a bed vs regular beds, transfers in/out, and
  whether the rest of the city is full (so the ward needs extra beds).
- At most 3 actions, each with a verb, an amount and a deadline: the Monday before the forecast
  week, but never sooner than 3 days after the data date.

Channels
- SMS: GSM-7 only (non-GSM characters are transliterated). One segment (160 septets) is the
  target; a second (306 septets in all) is used only for an extra critical fact: a bed transfer, a
  patient with no bed, or no spraying crew for a Critical ward.
- WhatsApp: a scannable chat message with *bold* section titles and a status emoji.
- Brief: self-contained A4 HTML (inline CSS, system fonts, no external URL). Page 1 is for decision
  makers; an optional page 2 explains the method in plain words.

format=json keeps the full structured context (every engine number, plus the plain-language text).
"""
from __future__ import annotations

import html
import math
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------- GSM-7 accounting
# GSM 03.38 basic character set (septet = 1) and extension table (escape + char = 2 septets).
GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXT = set("^{}\\[~]|€\f")
SMS_SINGLE, SMS_MULTI = 160, 153          # GSM-7 septets per segment
UCS2_SINGLE, UCS2_MULTI = 70, 67          # UCS-2 characters per segment
SMS_MAX_SEGMENTS = 2

# Common typography -> GSM-7 safe ASCII (so a notice never silently becomes UCS-2).
_TRANSLIT = {"–": "-", "—": "-", "−": "-", "→": "->", "←": "<-", "×": "x",
             "·": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "…": "...",
             "≥": ">=", "≤": "<=", " ": " "}


def to_gsm(text: str) -> str:
    out = []
    for ch in text:
        if ch in GSM7_BASIC or ch in GSM7_EXT:
            out.append(ch)
        elif ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        else:
            out.append("?")
    return "".join(out)


def sms_info(text: str) -> dict:
    """Encoding, length in septets / UCS-2 units, and segment count, as a phone would count it."""
    gsm = all(c in GSM7_BASIC or c in GSM7_EXT for c in text)
    if gsm:
        units = sum(2 if c in GSM7_EXT else 1 for c in text)
        single, multi, enc = SMS_SINGLE, SMS_MULTI, "GSM-7"
    else:
        units = sum(2 if ord(c) > 0xFFFF else 1 for c in text)   # astral chars use a surrogate pair
        single, multi, enc = UCS2_SINGLE, UCS2_MULTI, "UCS-2"
    segments = 1 if units <= single else math.ceil(units / multi)
    per = single if segments == 1 else multi
    return {"encoding": enc, "chars": len(text), "units": units, "segments": segments,
            "limit": per * segments, "remaining": per * segments - units,
            "single_limit": single, "multi_limit": multi}


# ---------------------------------------------------------------- small formatting helpers
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
SUPPLY_ORDER = ("saline", "kits", "beds")
SUPPLY_LABEL = {"saline": "Saline bags", "kits": "Diagnostic kits", "beds": "Hospital beds"}
SUPPLY_PLAIN = {"saline": ("saline bag", "saline bags"), "kits": ("test kit", "test kits"),
                "beds": ("extra bed", "extra beds")}
SUPPLY_TITLE = {"saline": "Saline bags", "kits": "Test kits", "beds": "Extra (surge) beds"}
SUPPLY_SMS = {"saline": ("saline", "saline"), "kits": ("kit", "kits"), "beds": ("extra bed", "extra beds")}
SUPPLY_SMS_SHORT = {**SUPPLY_SMS, "beds": ("bed", "beds")}     # SMS fallback when "extra" does not fit
TIER_ORDER = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
CRITICAL_DEFICIT, WARNING = "CRITICAL DEFICIT", "WARNING"
FLAG_MEANING = {CRITICAL_DEFICIT: "this high-risk ward will not get all the supplies it needs",
                WARNING: "this ward will not get all the supplies it needs"}
DEADLINE_LEAD_DAYS = 7      # act by the Monday before the forecast week ...
DEADLINE_MIN_DAYS = 3       # ... but never sooner than 3 days after the data date
SIGN = " -Dengue Watch"


def day(iso: str, year: bool = True) -> str:
    """'2025-07-28' -> '28 Jul 2025' (portable: never uses %-d)."""
    y, m, d = (int(x) for x in iso[:10].split("-"))
    return f"{d} {MONTHS[m - 1]}" + (f" {y}" if year else "")


def ward_name(ward_id: str) -> str:
    """'DNCC_Ward_05' -> 'DNCC Ward 05' (the city prefix keeps it unambiguous across cities)."""
    return ward_id.replace("_", " ")


def whole(x) -> int:
    """Round half up to a whole number (cases are counted, not measured)."""
    return int(math.floor(float(x) + 0.5))


def pct(x) -> str:
    return f"{whole(float(x) * 100)}%"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def join_and(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def cases_phrase(x) -> str:
    """'about 9 cases' / 'about 1 case' / 'fewer than 1 case'."""
    if float(x) < 1:
        return "fewer than 1 case"
    n = whole(x)
    return f"about {plural(n, 'case')}"


def range_phrase(lo, hi) -> str:
    """'likely 5 to 14' / 'up to 3' / '' when the range adds nothing."""
    lo_n, hi_n = whole(lo), whole(hi)
    if hi_n < 1:
        return ""
    if lo_n < 1:
        return f"up to {hi_n}"
    if lo_n == hi_n:
        return ""
    return f"likely {lo_n} to {hi_n}"


def deadline(issue_iso: str, target_iso: str) -> str:
    """The Monday before the forecast week, but at least DEADLINE_MIN_DAYS after the data date."""
    issue, target = date.fromisoformat(issue_iso[:10]), date.fromisoformat(target_iso[:10])
    return max(target - timedelta(days=DEADLINE_LEAD_DAYS), issue + timedelta(days=DEADLINE_MIN_DAYS)).isoformat()


def safe_name(text: str) -> str:
    """Windows/Linux-safe file stem: letters, digits, '-' and '_' only."""
    keep = "".join(c if c.isalnum() or c in "-_" else "_" for c in text.strip())
    return "_".join(p for p in keep.split("_") if p) or "notice"


def supply_amount(key: str, n: int, sms: bool = False, short: bool = False) -> str:
    one, many = (SUPPLY_SMS_SHORT if short else SUPPLY_SMS if sms else SUPPLY_PLAIN)[key]
    return plural(n, one, many)


def no_deadline(text: str, by: str) -> str:
    """WhatsApp puts the deadline in the section title, so its actions drop the trailing 'by <date>'."""
    return text.replace(f", by {by}.", ".").replace(f" by {by}.", ".")


# ---------------------------------------------------------------- contexts (numbers come from the payloads)
def _sim_rows(sim: dict | None) -> dict:
    return {} if not sim else {w["ward"]: w for w in sim.get("wards", [])}


def _ward_sim(sw: dict | None, h: int) -> dict | None:
    if not sw:
        return None
    at_h = next((x for x in sw["horizons"] if x["h"] == h), None)
    return {
        "crews": int(sw["crews"]), "reduction": sw["reduction"], "roi": sw["roi"],
        "tier": sw["tier"], "tier_after": sw["tier_after"],
        "prevented_cases": sw["prevented"]["cases"], "prevented_beds": sw["prevented"]["beds"],
        "prevented_saline": sw["prevented"]["saline"],
        "at_h": None if at_h is None else {
            "baseline": at_h["baseline"]["cases"], "scenario": at_h["scenario"]["cases"],
            "prevented": at_h["prevented"]["cases"], "tier_after": at_h["scenario"]["tier"]},
    }


def settings(alloc: dict, overflow: dict, sim: dict | None) -> dict:
    return {"stock_pct": alloc["stock_pct"], "floor_pct": alloc["floor_pct"], "crews_total": alloc["crews"]["total"],
            "crews_needed_pool": alloc["crews"]["needed"],
            "pool": alloc["scope_label"], "pool_scope": alloc["scope"], "pool_n": alloc["n_wards"],
            "capacity_pct": overflow["capacity_pct"], "basis": overflow["basis"],
            "rates": {s["key"]: s.get("k") for s in alloc.get("supplies", [])},
            "sim": None if not sim else {"effect_pct": sim["effect_pct"], "issue_date": sim["issue_date"],
                                         "crews": sim["crews_deployed"], "wards": sim["wards_deployed"]}}


def pool_words(st: dict) -> str:
    return f"all {st['pool_n']} wards" if st["pool_scope"] == "all" else f"{st['pool']}'s {st['pool_n']} wards"


def ward_context(ward_id: str, alloc: dict, overflow: dict, sim: dict | None = None,
                 city_beds: dict | None = None) -> dict:
    """Everything a ward notice says, copied from the engine payloads (no recomputation)."""
    a = next((w for w in alloc["wards"] if w["ward"] == ward_id), None)
    if a is None:
        raise KeyError(f"{ward_id} is not in the allocation scope")
    o = next((w for w in overflow["wards"] if w["ward"] == ward_id), None)
    rates = {s["key"]: s for s in alloc.get("supplies", [])}
    supplies = []
    for k in SUPPLY_ORDER:
        s = a["supplies"][k]
        supplies.append({"key": k, "label": SUPPLY_LABEL[k], "unit": rates.get(k, {}).get("unit", k),
                         "required": s["required"], "allocated": s["allocated"], "deficit": s["deficit"],
                         "coverage": s["coverage"], "safety": s["safety"]})
    beds = None
    if o is not None:
        tout = [{"to": t["to"], "to_name": ward_name(t["to"]), "patients": t["patients"]}
                for t in overflow["transfers"] if t["from"] == ward_id]
        tin = [{"from": t["from"], "from_name": ward_name(t["from"]), "patients": t["patients"]}
               for t in overflow["transfers"] if t["to"] == ward_id]
        beds = {k: o[k] for k in ("demand", "demand_expected", "demand_surge", "capacity", "baseline_capacity",
                                  "utilisation", "deficit", "headroom", "sent", "received", "load_after",
                                  "net_deficit", "utilisation_after", "status")}
        beds.update(basis=overflow["basis"], capacity_pct=overflow["capacity_pct"], transfers_out=tout, transfers_in=tin)
    if city_beds is None:
        city_beds = next((x for x in overflow["cities"] if x["city"] == a["city"]), None)
    city_n = sum(1 for w in alloc["wards"] if w["city"] == a["city"])
    city_rank = 1 + sum(1 for w in alloc["wards"] if w["city"] == a["city"] and w["priority"] < a["priority"])
    c = {
        "scope": "ward", "ward": ward_id, "name": ward_name(ward_id), "label": a["label"], "city": a["city"],
        "issue_date": alloc["issue_date"], "target_date": alloc["target_date"], "horizon": alloc["horizon"],
        "deadline": deadline(alloc["issue_date"], alloc["target_date"]),
        "tier": a["tier"], "expected": a["expected"], "low_80": a["low_80"], "high_80": a["high_80"],
        "priority": a["priority"], "priority_of": alloc["n_wards"], "pool": alloc["scope_label"],
        "city_rank": city_rank, "city_n": city_n,
        "supplies": supplies, "flag": a["flag"],
        "crews_needed": a["crews_needed"], "crews_assigned": a["crews_assigned"],
        "beds": beds, "city_beds": city_beds, "sim": _ward_sim(_sim_rows(sim).get(ward_id), alloc["horizon"]),
        "settings": settings(alloc, overflow, sim),
    }
    c["reasons"] = ward_reasons(c)
    c["alerts"] = ward_alerts(c)
    c["actions"] = ward_actions(c)
    c["bed_story"] = bed_story(c)
    c["summary"] = ward_in_short(c)
    return c


def city_free_beds(cb: dict | None) -> int | None:
    """Free beds left after routing in wards that can still take patients (Critical wards keep theirs)."""
    return None if not cb else cb["remaining_headroom"] - cb["headroom_reserved"]


def ward_reasons(c: dict) -> dict:
    """One plain line explaining each shortage (why the ward did not get everything)."""
    st = c["settings"]
    ahead = c["priority"] - 1
    out = {"supplies": None, "crews": None}
    if any(s["deficit"] for s in c["supplies"]):
        out["supplies"] = (f"Stock this week covers {st['stock_pct']}% of what {pool_words(st)} need"
                           + (f"; it went first to {plural(ahead, 'ward')} with more risk or crowding." if ahead
                              else "."))
    gap = c["crews_needed"] - c["crews_assigned"]
    if gap > 0:
        out["crews"] = f"All {st['crews_total']} crews in the pool went first to wards with more risk or crowding."
    return out


def ward_alerts(c: dict) -> list[dict]:
    """Alert lines. level = CRITICAL DEFICIT / WARNING (severity); label is the official flag printed first."""
    out = []
    short = [s for s in c["supplies"] if s["deficit"] > 0]
    if c["flag"]:
        detail = "; ".join(f"{s['label'].lower()} {s['allocated']}/{s['required']} ({pct(s['coverage'])}), short {s['deficit']}"
                           for s in short)
        plain = f"{c['flag']}: {FLAG_MEANING[c['flag']]}. {c['reasons']['supplies']}"
        out.append({"level": c["flag"], "label": c["flag"], "kind": "supply", "detail": detail,
                    "text": f"{c['flag']}: {detail}.", "plain": plain})
    b = c["beds"]
    if b and b["net_deficit"] > 0:
        n = b["net_deficit"]
        detail = (f"{b['demand']} beds needed vs {b['capacity']} capacity; "
                  f"{plural(n, 'patient')} still without a bed after same-city routing")
        plain = (f"BED OVERFLOW: {plural(n, 'patient')} may have no bed; "
                 f"no other ward in {c['city']} has a free regular bed, so the ward needs extra beds.")
        out.append({"level": CRITICAL_DEFICIT if TIER_ORDER[c["tier"]] >= 2 else WARNING, "label": "BED OVERFLOW",
                    "kind": "beds", "detail": detail, "text": f"BED OVERFLOW: {detail}.", "plain": plain})
    return out


def _act(rank: int, key: str, text: str, sms: str, chat: str | None = None, by: str = "") -> tuple:
    """text = brief (verb + amount + deadline); sms = short form; chat = WhatsApp (same amounts, no deadline)."""
    return (rank, {"key": key, "text": text, "sms": sms, "chat": chat if chat is not None else no_deadline(text, by)})


def ward_actions(c: dict) -> list[dict]:
    """Recommended actions, most urgent first. text = brief (verb + amount + deadline), sms = short form with
    deadline, chat = WhatsApp form (the deadline is in the section title, amounts are stated above it)."""
    by = day(c["deadline"], False)
    tier, b = c["tier"], c["beds"]
    short = [s for s in c["supplies"] if s["deficit"] > 0]
    acts = []
    if b and b["net_deficit"] > 0:
        n = b["net_deficit"]
        acts.append(_act(10, "beds_escalate", f"Arrange {plural(n, 'extra bed')} at the city hospital by {by}.",
                         f"Book {plural(n, 'extra bed')} at the city hospital by {by}.", by=by))
    if short:
        items = join_and([supply_amount(s["key"], s["deficit"]) for s in short])
        rank = 20 if c["flag"] == CRITICAL_DEFICIT else 70
        acts.append(_act(rank, "resupply", f"Request {items} from the regional store by {by}.",
                         f"Resupply by {by}.", by=by))
    if b and b["transfers_out"]:
        to = join_and([f"{plural(t['patients'], 'patient')} to {t['to_name']}" for t in b["transfers_out"]])
        first = b["transfers_out"][0]
        acts.append(_act(30, "transfer_out", f"Phone ahead, then move {to} by {by}.",
                         f"Send {plural(first['patients'], 'patient')} to {first['to_name']} by {by}.", by=by))
    crews = c["crews_assigned"]
    with_crews = f" with the {plural(crews, 'assigned crew')}" if crews else ""
    if tier == "Critical":
        acts.append(_act(40, "tier", f"Open a fever corner and start daily larviciding{with_crews} by {by}.",
                         f"Open a fever corner by {by}.", by=by))
    elif tier == "High":
        acts.append(_act(45, "tier", f"Start door-to-door checks for mosquito breeding sites{with_crews} by {by}.",
                         f"Start door-to-door checks by {by}.", by=by))
    gap = c["crews_needed"] - crews
    if gap > 0:
        acts.append(_act(50, "crews", f"Ask the city for {plural(gap, 'spraying crew')} by {by}.",
                         f"Ask for {plural(gap, 'more crew')} by {by}.", by=by))
    if b and b["transfers_in"]:
        frm = join_and([t["from_name"] for t in b["transfers_in"]])
        acts.append(_act(60, "transfer_in", f"Get {plural(b['received'], 'regular bed')} ready for patients from {frm} by {by}.",
                         f"Ready {plural(b['received'], 'regular bed')} by {by}.", by=by))
    if tier == "Medium":
        acts.append(_act(80, "tier", f"Run weekly larviciding{with_crews} and clear standing water by {by}.",
                         f"Run larviciding by {by}.", by=by))
    elif tier == "Low":
        acts.append(_act(90, "tier", f"Keep routine checks; no extra action is needed before {by}.",
                         "Keep routine checks.", "Keep routine checks; no extra action needed."))
    acts.sort(key=lambda a: a[0])
    return [a for _, a in acts]


def bed_need_phrase(b: dict, with_normal: bool = True) -> str:
    """'Up to 4 patients may need a bed in a bad week (normally 3)' or 'About 3 patients may need a bed'."""
    if b["basis"] == "surge":
        s = f"Up to {plural(b['demand_surge'], 'patient')} may need a bed in a bad week"
        if with_normal and b["demand_expected"] != b["demand_surge"]:
            s += f" (normally {b['demand_expected']})"
        return s
    return f"About {plural(b['demand_expected'], 'patient')} may need a bed"


def bed_story(c: dict, with_normal: bool = True) -> str:
    """One plain story: patients who may need a bed vs the ward's regular beds, transfers in/out, and whether
    the other wards of the city are full (so the ward needs extra beds)."""
    b = c["beds"]
    if not b:
        return ""
    d, cap = b["demand"], b["capacity"]
    regular = plural(cap, "regular bed")
    if d == 0:
        s = f"No beds should be needed this week; the ward has {regular}"
    else:
        s = bed_need_phrase(b, with_normal)
        if d < cap:
            s += f"; its {regular} {'is' if cap == 1 else 'are'} enough"
        elif d == cap:
            s += ", which fills its 1 regular bed" if cap == 1 else f", which fills all {cap} regular beds"
        else:
            s += f", but the ward has only {regular}"
    moves = []
    if b["transfers_out"]:
        moves.append("Send " + join_and([f"{plural(t['patients'], 'patient')} to {t['to_name']}" for t in b["transfers_out"]]))
    if b["transfers_in"]:
        moves.append("Expect " + join_and([f"{plural(t['patients'], 'patient')} from {t['from_name']}" for t in b["transfers_in"]]))
    free = city_free_beds(c.get("city_beds"))
    city = ""
    if b["net_deficit"] > 0:
        city = (f"other {c['city']} wards have {'no more free beds' if b['transfers_out'] else 'none free'}, so "
                f"{plural(b['net_deficit'], 'patient')} may have no bed and the ward needs extra beds")
    elif d >= cap and d > 0 and free == 0:
        city = (f"other {c['city']} wards have {'no more free beds' if b['transfers_out'] else 'none free'}, "
                "so the ward needs extra beds")
    if not moves:
        return s + (f"; {city}." if city else ".")
    tail = "; ".join(moves[:1] + ([city] if city else []))
    return s + ". " + tail + "." + "".join(f" {m}." for m in moves[1:])


def ward_in_short(c: dict) -> str:
    """One-to-two sentences: the situation, then the single most important ask."""
    tier = c["tier"].lower()
    sit = f"{cases_phrase(c['expected']).capitalize()} of dengue {'is' if whole(c['expected']) == 1 or c['expected'] < 1 else 'are'} expected in the week of {day(c['target_date'], False)} ({tier} risk)"
    problems = []
    short = [s for s in c["supplies"] if s["deficit"] > 0]
    if short and all(s["allocated"] == 0 for s in c["supplies"] if s["required"]):
        problems.append("none of the ward's supplies are allocated yet")
    elif short:
        worst = min(short, key=lambda s: s["coverage"])
        problems.append(f"it is getting only {pct(worst['coverage'])} of the {SUPPLY_PLAIN[worst['key']][1]} it needs")
    b = c["beds"]
    if b and b["net_deficit"] > 0:
        problems.append(f"{plural(b['net_deficit'], 'patient')} may have no bed")
    elif b and b["transfers_out"]:
        problems.append(f"{plural(b['sent'], 'patient')} must be moved to another ward's hospital")
    if not problems:
        problems.append("supplies and beds are covered")
    s = sit + ", and " + join_and(problems) + "."
    if c["actions"]:
        a = c["actions"][0]["text"]
        s += " Most important: " + a[0].lower() + a[1:]
    return s


def city_context(city: str, alloc: dict, overflow: dict, sim: dict | None = None,
                 only: list[str] | None = None) -> dict:
    """A city brief: every ward of the city (or just `only`), in allocation priority order."""
    ids = [w["ward"] for w in alloc["wards"] if w["city"] == city and (only is None or w["ward"] in only)]
    cs = next((x for x in overflow["cities"] if x["city"] == city), None)
    wards = [ward_context(w, alloc, overflow, sim, city_beds=cs) for w in ids]
    sup = {}
    for k in SUPPLY_ORDER:
        req = sum(next(s for s in w["supplies"] if s["key"] == k)["required"] for w in wards)
        got = sum(next(s for s in w["supplies"] if s["key"] == k)["allocated"] for w in wards)
        sup[k] = {"key": k, "label": SUPPLY_LABEL[k], "required": req, "allocated": got, "deficit": req - got,
                  "coverage": round(got / req, 4) if req else 1.0}
    tiers = {t: sum(w["tier"] == t for w in wards) for t in TIER_ORDER}
    transfers = [{**t, "from_name": ward_name(t["from"]), "to_name": ward_name(t["to"])}
                 for t in overflow["transfers"] if t["city"] == city and
                 (only is None or t["from"] in only or t["to"] in only)]
    sims = [w["sim"] for w in wards if w["sim"]]
    c = {
        "scope": "city" if only is None else "ward", "city": city, "name": city,
        "issue_date": alloc["issue_date"], "target_date": alloc["target_date"], "horizon": alloc["horizon"],
        "deadline": deadline(alloc["issue_date"], alloc["target_date"]),
        "n_wards": len(wards), "wards": wards, "tiers": tiers,
        "expected": round(sum(w["expected"] for w in wards), 2),
        "supplies": [sup[k] for k in SUPPLY_ORDER],
        "n_critical_deficit": sum(w["flag"] == CRITICAL_DEFICIT for w in wards),
        "n_warning": sum(w["flag"] == WARNING for w in wards),
        "crews_needed": sum(w["crews_needed"] for w in wards), "crews_assigned": sum(w["crews_assigned"] for w in wards),
        "beds": cs, "transfers": transfers,
        "sim": None if not sims else {
            "crews": sum(s["crews"] for s in sims), "wards": len(sims),
            "prevented_cases": round(sum(s["prevented_cases"] for s in sims), 3),
            "prevented_beds": sum(s["prevented_beds"] for s in sims),
            "prevented_saline": sum(s["prevented_saline"] for s in sims)},
        "settings": settings(alloc, overflow, sim),
    }
    c["urgent"] = [w["ward"] for w in urgent_wards(c)]
    c["actions"] = city_actions(c)
    c["bed_story"] = city_bed_story(c)
    c["summary"] = city_in_short(c)
    return c


def urgency(w: dict) -> tuple:
    b = w["beds"] or {}
    unplaced = b.get("net_deficit", 0) > 0
    level = 0 if (w["flag"] == CRITICAL_DEFICIT or unplaced) else 1 if (
        w["flag"] == WARNING or b.get("transfers_out") or w["crews_assigned"] < w["crews_needed"]) else 2
    short = sum(s["deficit"] for s in w["supplies"])
    return (level, -TIER_ORDER[w["tier"]], -int(unplaced), -short, -w["expected"], w["priority"])


def urgent_wards(c: dict, limit: int | None = None) -> list[dict]:
    """Wards that need someone to act, most urgent first (shortages, beds, crews, then risk)."""
    ws = [w for w in c["wards"] if urgency(w)[0] < 2 or TIER_ORDER[w["tier"]] >= 2]
    ws.sort(key=urgency)
    return ws[:limit] if limit else ws


def city_actions(c: dict) -> list[dict]:
    by = day(c["deadline"], False)
    acts = []
    crit = [w for w in c["wards"] if w["flag"] == CRITICAL_DEFICIT]
    if crit:
        names = join_and([w["name"] for w in crit[:3]] + ([f"{len(crit) - 3} more"] if len(crit) > 3 else []))
        acts.append(_act(10, "release_stock", f"Release emergency stock to the {plural(len(crit), 'high-risk ward')} "
                         f"short of supplies ({names}) by {by}.",
                         f"Release stock to {plural(len(crit), 'ward')} by {by}.",
                         f"Release emergency stock to the {plural(len(crit), 'high-risk ward')} short of supplies."))
    b = c["beds"]
    if b and b["unresolved"] > 0:
        acts.append(_act(20, "beds_escalate", f"Arrange {plural(b['unresolved'], 'extra bed')} at the city or "
                         f"regional hospital by {by}.", f"Find {plural(b['unresolved'], 'extra bed')} by {by}.", by=by))
    if c["transfers"]:
        n = sum(t["patients"] for t in c["transfers"])
        acts.append(_act(30, "transfers", f"Coordinate the move of {plural(n, 'patient')} between ward hospitals by {by}.",
                         f"Move {plural(n, 'patient')} by {by}.", by=by))
    gap = c["crews_needed"] - c["crews_assigned"]
    if gap > 0:
        acts.append(_act(40, "crews", f"Find {plural(gap, 'more spraying crew')} by {by}.",
                         f"Find {plural(gap, 'more crew')} by {by}.", by=by))
    warn = [w for w in c["wards"] if w["flag"] == WARNING]
    if warn:
        acts.append(_act(50, "top_up", f"Top up the {plural(len(warn), 'ward')} with a WARNING at the next delivery, by {by}.",
                         f"Top up {plural(len(warn), 'ward')} by {by}.", by=by))
    hot = [w for w in c["wards"] if TIER_ORDER[w["tier"]] >= 2]
    if hot:
        acts.append(_act(60, "tier", f"Start daily larviciding and door-to-door checks in the {plural(len(hot), 'high-risk ward')} by {by}.",
                         f"Start larviciding by {by}.", by=by))
    if not acts:
        acts.append(_act(90, "routine", "Keep routine checks and weekly larviciding; no shortfalls this week.",
                         "Keep routine checks.", "Keep routine checks and weekly larviciding."))
    acts.sort(key=lambda a: a[0])
    return [a for _, a in acts]


def city_bed_story(c: dict) -> str:
    b = c["beds"]
    if not b:
        return ""
    st = c["settings"]
    need = "may need a bed in a bad week" if st["basis"] == "surge" else "may need a bed"
    s = f"Up to {plural(b['demand'], 'patient')} {need}" if st["basis"] == "surge" else f"About {plural(b['demand'], 'patient')} {need}"
    if b["demand"] > b["capacity"]:
        s += f", more than the {b['capacity']} regular beds in {c['city']}'s wards."
    elif b["demand"] == b["capacity"]:
        s += f", which fills all {b['capacity']} regular beds in {c['city']}'s wards."
    else:
        s += f"; {c['city']}'s wards have {b['capacity']} regular beds."
    if b["transferred"]:
        s += f" {plural(b['transferred'], 'patient')} can be moved to wards with free regular beds."
    if b["unresolved"]:
        s += (f" {plural(b['unresolved'], 'patient')} may have no bed: every ward's regular beds are full, "
              "so the city needs extra beds.")
    elif b["overflow_before"] == 0:
        s += " No transfers are needed."
    return s


def city_in_short(c: dict) -> str:
    t = c["tiers"]
    risk = join_and([f"{t['Critical']} at critical" if t["Critical"] else "", f"{t['High']} at high risk" if t["High"] else ""])
    s = (f"{cases_phrase(c['expected']).capitalize()} of dengue are expected across {c['city']}'s "
         f"{c['n_wards']} wards in the week of {day(c['target_date'], False)}"
         + (f"; {risk}" if risk else "") + ".")
    problems = []
    if c["n_critical_deficit"]:
        problems.append(f"{plural(c['n_critical_deficit'], 'high-risk ward')} will not get all the supplies they need")
    if c["beds"] and c["beds"]["unresolved"]:
        problems.append(f"{plural(c['beds']['unresolved'], 'patient')} may have no bed")
    if problems:
        s += " " + join_and(problems).capitalize() + "."
    if c["actions"]:
        a = c["actions"][0]["text"]
        if " (" in a and a.endswith(")" + a.rsplit(")", 1)[-1]):      # drop the list of ward names
            a = a.split(" (", 1)[0] + a.rsplit(")", 1)[-1]
        s += " Most important: " + a[0].lower() + a[1:]
    return s


# ---------------------------------------------------------------- SMS
def _fit(variants: list[str], max_segments: int) -> str | None:
    for v in variants:
        v = to_gsm(v)
        if sms_info(v)["segments"] <= max_segments:
            return v
    return None


def _sms_supplies(c: dict) -> list[list[str]]:
    """Shortage phrases, one group per level of detail (fullest first). Within a group the wording only
    shortens ("3 extra beds" -> "3 beds"); moving to the next group drops facts."""
    short = [s for s in c["supplies"] if s["deficit"] > 0]
    if not short:
        return [[" Supplies are covered."], [""]]
    need = [s for s in c["supplies"] if s["required"]]

    def items(rows, field, sh):
        return ", ".join(supply_amount(s["key"], s[field], sms=True, short=sh) for s in rows)

    if all(s["allocated"] == 0 for s in need):
        return [[f" Need {items(need, 'required', sh)}, none allocated yet." for sh in (False, True)],
                [f" Need {items(need, 'required', sh)}, none allocated." for sh in (False, True)],
                [" No supplies allocated yet."]]
    worst = max(short, key=lambda s: (1 - s["coverage"], s["deficit"]))
    return [[f" Short {items(short, 'deficit', sh)}." for sh in (False, True)],
            [f" Short {items([worst], 'deficit', sh)}." for sh in (False, True)]]


def _sms_extras(c: dict) -> list[str]:
    """Extra critical facts that justify a second segment (never one the action already states)."""
    out, b = [], c["beds"]
    first = c["actions"][0]["key"] if c["actions"] else ""
    if b and b["net_deficit"] > 0 and first != "beds_escalate":
        out.append(f" {plural(b['net_deficit'], 'patient')} may have no bed.")
    if b and b["transfers_out"] and first != "transfer_out":
        t = b["transfers_out"][0]
        out.append(f" Send {plural(b['sent'], 'patient')} to {t['to_name']}.")
    if b and b["transfers_in"] and first != "transfer_in":
        out.append(f" Expect {plural(b['received'], 'patient')} from {b['transfers_in'][0]['from_name']}.")
    if c["tier"] == "Critical" and c["crews_needed"] and c["crews_assigned"] == 0:
        out.append(" No spraying crew yet.")
    return out


SMS_FLAG = {CRITICAL_DEFICIT: "CRITICAL", WARNING: "WARNING"}


def sms_ward(c: dict) -> str:
    """Deficit wards open with their flag ("CRITICAL: DNCC Ward 01 dengue, ..."), others with "Dengue alert".
    Variants are tried from fullest to shortest: shorter wording first, then without the signature, and
    only then with fewer facts."""
    rng = whole(c["high_80"])
    cases = cases_phrase(c["expected"])
    wk = day(c["target_date"], False)
    up = f" (up to {rng})" if rng > max(whole(c["expected"]), 0) else ""
    if c["flag"]:
        heads = [f"{SMS_FLAG[c['flag']]}: {c['name']} dengue, {cases} wk of {wk}{up}."]
    else:
        alert = TIER_ORDER[c["tier"]] >= 2 or (c["beds"] and c["beds"]["net_deficit"])
        word = "Dengue alert" if alert else "Dengue update"
        heads = [f"{word} {c['name']}: {cases} expected wk of {wk}{up}.",
                 f"{word} {c['name']}: {cases} wk of {wk}{up}."]
    act = f" {c['actions'][0]['sms']}" if c["actions"] else ""

    def variants(extras: str, groups: list[list[str]]) -> list[str]:
        return [h + s + extras + act + sign for group in groups for sign in (SIGN, "")
                for h in heads for s in group]

    groups = _sms_supplies(c)
    extras = "".join(_sms_extras(c))
    one = variants("", groups)
    if extras:   # a critical extra fact may use a second segment rather than cost the supply facts
        msg = _fit(variants(extras, groups[:1]), 1) or _fit(variants(extras, groups), SMS_MAX_SEGMENTS)
    else:
        msg = _fit(one, 1)
    return msg or _fit(one + [heads[-1] + act], SMS_MAX_SEGMENTS) or to_gsm(heads[-1])


def sms_city(c: dict) -> str:
    wk = day(c["target_date"], False)
    t = c["tiers"]
    hot = t["Critical"] + t["High"]
    head = f"Dengue alert {c['city']}: {cases_phrase(c['expected'])} expected wk of {wk}"
    risk = [f", {plural(hot, 'ward')} at high risk." if hot else "."]
    b = c["beds"]
    probs = []
    if c["n_critical_deficit"] or c["n_warning"]:
        probs.append(f"{plural(c['n_critical_deficit'] + c['n_warning'], 'ward')} short of supplies")
    if b and b["unresolved"]:
        probs.append(f"{plural(b['unresolved'], 'patient')} may lack a bed")
    prob = (" " + join_and(probs).capitalize() + ".") if probs else " Supplies and beds are covered."
    act = f" {c['actions'][0]['sms']}" if c["actions"] else ""
    short_head = f"Dengue alert {c['city']}: {cases_phrase(c['expected'])} wk of {wk}"
    variants = [head + risk[0] + prob + act + SIGN, head + "." + prob + act + SIGN, short_head + "." + prob + act + SIGN]
    return _fit(variants, 1) or _fit(variants, SMS_MAX_SEGMENTS) or to_gsm(head + "." + SIGN)


# ---------------------------------------------------------------- WhatsApp
def status_emoji(flag: str | None, c: dict) -> str:
    b = c.get("beds") or {}
    if flag == CRITICAL_DEFICIT or b.get("net_deficit", 0) > 0 or b.get("unresolved", 0) > 0:
        return "\U0001F534"          # red circle
    if flag == WARNING or b.get("transfers_out") or c.get("transfers"):
        return "\U0001F7E0"          # orange circle
    return "\U0001F7E2"              # green circle


def supply_line(supplies: list[dict]) -> str:
    """'Saline bags 21 / 0, test kits 10 / 0, extra (surge) beds 3 / 0' (need / getting)."""
    return ", ".join(f"{SUPPLY_TITLE[s['key']] if i == 0 else SUPPLY_TITLE[s['key']].lower()} "
                     f"{s['required']} / {s['allocated']}" for i, s in enumerate(supplies))


def whatsapp_ward(c: dict) -> str:
    rng = range_phrase(c["low_80"], c["high_80"])
    supplies = supply_line(c["supplies"])
    L = [f"{status_emoji(c['flag'], c)} *Dengue alert: {c['name']}, {c['city']}*", "",
         "*What to expect*",
         f"{cases_phrase(c['expected']).capitalize()} of dengue{f' ({rng})' if rng else ''}. {c['tier']} risk.", "",
         "*Supplies: need / getting*", supplies + "."]
    if c["flag"]:
        L.append(f"{c['flag']}: {FLAG_MEANING[c['flag']]}. {c['reasons']['supplies']}")
    if c["beds"]:
        L += ["", "*Hospital beds*", bed_story(c, with_normal=False)]
    L += ["", "*Spraying crews*"]
    if c["crews_needed"] == 0:
        L.append("None needed this week.")
    elif c["crews_assigned"] >= c["crews_needed"]:
        L.append(f"All {c['crews_needed']} needed are assigned." if c["crews_needed"] > 1 else "The 1 crew needed is assigned.")
    elif c["crews_assigned"] == 0:
        L.append(f"None assigned yet ({c['crews_needed']} needed). {c['reasons']['crews']}")
    else:
        L.append(f"{c['crews_assigned']} of {c['crews_needed']} needed are assigned.")
    if c["sim"]:
        L.append(f"Spraying should prevent {cases_phrase(c['sim']['prevented_cases'])} over the next 2 to 4 weeks.")
    L += ["", f"*Please do by {day(c['deadline'], False)}*"]
    L += [f"{i}. {a['chat']}" for i, a in enumerate(c["actions"][:3], 1)]
    L += ["", f"_Dengue Watch forecast for the week of {day(c['target_date'])}, from data up to {day(c['issue_date'], False)}._"]
    return "\n".join(L)


def whatsapp_city(c: dict) -> str:
    t = c["tiers"]
    risk = join_and([f"{t['Critical']} at critical" if t["Critical"] else "", f"{t['High']} at high risk" if t["High"] else ""])
    supplies = supply_line(c["supplies"])
    flag = CRITICAL_DEFICIT if c["n_critical_deficit"] else WARNING if c["n_warning"] else None
    L = [f"{status_emoji(flag, c)} *Dengue summary: {c['city']}*", "",
         "*What to expect*",
         f"{cases_phrase(c['expected']).capitalize()} of dengue across {plural(c['n_wards'], 'ward')}"
         + (f"; {risk}." if risk else "."), "",
         "*Supplies: need / getting*", supplies + "."]
    if c["n_critical_deficit"]:
        crit = [w["name"] for w in c["wards"] if w["flag"] == CRITICAL_DEFICIT]
        L.append(f"CRITICAL DEFICIT in {plural(len(crit), 'ward')}: these high-risk wards will not get all the supplies they need"
                 f" ({join_and(crit[:3] + ([f'{len(crit) - 3} more'] if len(crit) > 3 else []))}).")
    if c["n_warning"]:
        L.append(f"WARNING in {plural(c['n_warning'], 'ward')}: lower-risk wards that will not get everything they need.")
    if flag:
        st = c["settings"]
        L.append(f"Stock this week covers {st['stock_pct']}% of what {pool_words(st)} need; it goes first to wards with more risk or crowding.")
    if c["beds"]:
        L += ["", "*Hospital beds*", c["bed_story"]]
        L += [f"{x['from_name']} -> {x['to_name']}: {plural(x['patients'], 'patient')}" for x in c["transfers"][:3]]
    L += ["", "*Spraying crews*",
          "None needed this week." if not c["crews_needed"] else
          f"{c['crews_assigned']} of {c['crews_needed']} needed are assigned."]
    L += ["", f"*Please do by {day(c['deadline'], False)}*"]
    L += [f"{i}. {a['chat']}" for i, a in enumerate(c["actions"][:3], 1)]
    L += ["", f"_Dengue Watch forecast for the week of {day(c['target_date'])}, from data up to {day(c['issue_date'], False)}._"]
    return "\n".join(L)


# ---------------------------------------------------------------- printable brief
BRIEF_CSS = """
:root{--ink:#16161a;--ink2:#46464f;--ink3:#6b6b75;--line:#d6d6dc;--soft:#f5f5f7;--hi:#f1f6f8;
--crit:#b3261e;--warn:#9a5b00;--ok:#1d6b3a}
*{box-sizing:border-box}
html{background:#e7e7ec}
body{margin:0;font:10.5pt/1.5 "Segoe UI","Helvetica Neue",Arial,system-ui,-apple-system,Roboto,sans-serif;color:var(--ink);
-webkit-print-color-adjust:exact;print-color-adjust:exact}
.page{width:210mm;min-height:297mm;margin:14px auto;background:#fff;padding:16mm 16mm 14mm;box-shadow:0 2px 16px rgba(0,0,0,.12);
display:flex;flex-direction:column}
.page+.page{margin-top:18px}
.num{font-variant-numeric:tabular-nums}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;padding-bottom:10px;border-bottom:2px solid var(--ink)}
.kicker{font-size:8.5pt;letter-spacing:.09em;text-transform:uppercase;color:var(--ink3);font-weight:600}
h1{font-size:20pt;line-height:1.15;margin:3px 0 2px;letter-spacing:-.01em;font-weight:700}
.week{font-size:11pt;color:var(--ink2)}
.week b{color:var(--ink)}
.status{border:1.5px solid currentColor;border-radius:6px;padding:4px 10px;font-weight:700;font-size:9.5pt;white-space:nowrap;text-align:center}
.status small{display:block;font-weight:500;font-size:8pt}
.s-crit{color:var(--crit)}.s-warn{color:var(--warn)}.s-ok{color:var(--ok)}
.inshort{margin:14px 0 4px;padding:12px 14px;background:var(--hi);border-left:4px solid var(--ink);font-size:12pt;line-height:1.45}
.inshort b{font-weight:700}
.figs{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0 6px}
.fig{border:1px solid var(--line);border-radius:8px;padding:9px 11px}
.fig span{display:block;font-size:8.5pt;color:var(--ink3);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.fig b{display:block;font-size:20pt;font-weight:650;line-height:1.15;margin-top:2px}
.fig small{font-size:8.5pt;color:var(--ink2)}
.fig.bad{border:1.5px solid var(--crit)}.fig.bad b{color:var(--crit)}
h2{font-size:11pt;margin:16px 0 6px;font-weight:700;break-after:avoid}
table{width:100%;border-collapse:collapse;font-size:10pt}
th{text-align:left;font-weight:600;color:var(--ink2);border-bottom:1.5px solid var(--ink);padding:5px 7px;font-size:9pt}
td{border-bottom:1px solid var(--line);padding:6px 7px;vertical-align:top}
.r{text-align:right}
tr{break-inside:avoid;page-break-inside:avoid}
td.short{font-weight:700}
.flagline{margin-top:7px;font-size:10pt}
.flag{display:inline-block;font-weight:800;letter-spacing:.03em;border:1.5px solid currentColor;border-radius:4px;padding:0 5px;font-size:8.5pt;margin-right:4px}
p{margin:4px 0}
.muted{color:var(--ink3)}
ul.todo{list-style:none;margin:4px 0 0;padding:0}
ul.todo li{display:flex;gap:10px;align-items:flex-start;padding:6px 0;border-bottom:1px solid var(--line);break-inside:avoid}
ul.todo li::before{content:"";flex:none;width:13px;height:13px;border:1.5px solid var(--ink);border-radius:2px;margin-top:3px}
ul.tx{margin:4px 0;padding-left:18px}
table.wt{font-size:9.5pt}table.wt td{padding:4px 6px}table.wt th{padding:4px 6px}
.nw{white-space:nowrap}.st{font-size:8.5pt;font-weight:700;letter-spacing:.02em;white-space:nowrap}
.sign{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:22px;font-size:9.5pt;color:var(--ink2)}
.sign div{border-top:1px solid var(--ink);padding-top:4px}
.spacer{flex:1}
.foot{margin-top:14px;font-size:8.5pt;color:var(--ink3);border-top:1px solid var(--line);padding-top:6px;display:flex;justify-content:space-between;gap:12px}
.method h2{margin-top:14px}
.method p{max-width:165mm}
.noprint{position:sticky;top:0;z-index:2;display:flex;justify-content:center;gap:8px;padding:8px;background:#e7e7ec}
.noprint button{font:inherit;font-size:10pt;padding:6px 14px;border-radius:6px;border:1px solid #888;background:#fff;color:#111;cursor:pointer}
@page{size:A4;margin:0}
@media print{html{background:#fff}.noprint{display:none}.page,.page+.page{margin:0;box-shadow:none;width:auto;min-height:276mm;break-after:page}
.page:last-child{break-after:auto}thead{display:table-header-group}
.page{padding:13mm 15mm 11mm}.inshort{font-size:11pt;margin-top:10px;padding:9px 12px}.figs{margin:10px 0 4px}.fig{padding:7px 10px}.fig b{font-size:17pt}h2{margin:11px 0 4px}td{padding:4px 7px}ul.todo li{padding:4px 0}.sign{margin-top:14px}body{font-size:9.8pt;line-height:1.36}td,table.wt td{padding:3px 6px}th{padding:3px 6px}.fig b{font-size:16pt;margin:0}.fig{padding:6px 9px}.inshort{line-height:1.4}.inshort{font-size:10.5pt}.fig small,.fig span{font-size:8pt}.top h1{font-size:18pt}.flagline,.muted{margin:2px 0}table,table.wt{font-size:9pt}h2{margin:8px 0 3px;font-size:10.5pt}.figs{margin:7px 0 2px}.inshort{margin-top:8px;padding:7px 11px}.sign{margin-top:10px}ul.todo li{padding:3px 0}.top{padding-bottom:7px}.foot{margin-top:8px}}
@media screen and (max-width:820px){.page{width:auto;min-height:0;margin:0 0 12px;padding:18px}.figs{grid-template-columns:1fr 1fr}
.top{flex-direction:column}.sign{grid-template-columns:1fr}}
"""


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _status(flag: str | None, tier: str, beds_short: bool) -> str:
    if flag == CRITICAL_DEFICIT or beds_short:
        return f'<div class="status s-crit">{"CRITICAL DEFICIT" if flag == CRITICAL_DEFICIT else "BED SHORTAGE"}<small>{_e(tier)} risk</small></div>'
    if flag == WARNING:
        return f'<div class="status s-warn">WARNING<small>{_e(tier)} risk</small></div>'
    return f'<div class="status s-ok">Covered<small>{_e(tier)} risk</small></div>'


def _fig(label: str, value: str, sub: str, bad: bool = False) -> str:
    return f'<div class="fig{" bad" if bad else ""}"><span>{_e(label)}</span><b class="num">{_e(value)}</b><small>{_e(sub)}</small></div>'


def _supply_table(supplies: list[dict]) -> str:
    rows = "".join(
        f"<tr><td>{_e(SUPPLY_TITLE[s['key']])}</td><td class=\"r num\">{s['required']}</td>"
        f"<td class=\"r num\">{s['allocated']}</td>"
        f"<td class=\"r num{' short' if s['deficit'] else ''}\">{s['deficit'] if s['deficit'] else '&ndash;'}</td></tr>"
        for s in supplies)
    return ('<table><thead><tr><th>Supply</th><th class="r">Need</th><th class="r">Getting</th>'
            f'<th class="r">Short</th></tr></thead><tbody>{rows}</tbody></table>')


def _todo(actions: list[dict]) -> str:
    return '<ul class="todo">' + "".join(f"<li><span>{_e(a['text'])}</span></li>" for a in actions[:3]) + "</ul>"


def _sign_and_foot(c: dict, gen: str) -> str:
    return ('<div class="spacer"></div><div class="sign"><div>Prepared by</div><div>Approved by</div><div>Date</div></div>'
            '<div class="foot"><span>Forecast from weather and past case data by Dengue Watch. Numbers are estimates; '
            'see page 2 for method.</span>'
            f'<span class="num">Generated {gen} from data up to {day(c["issue_date"])}</span></div>')


def _top(kicker: str, title: str, c: dict, status: str) -> str:
    return (f'<div class="top"><div><div class="kicker">{_e(kicker)}</div><h1>{_e(title)}</h1>'
            f'<div class="week">Forecast for the week of <b>{day(c["target_date"])}</b></div></div>{status}</div>')


def _lowest_cover(supplies: list[dict]) -> tuple[str, str, bool]:
    need = [s for s in supplies if s["required"]]
    if not need:
        return "100%", "nothing needed", False
    worst = min(need, key=lambda s: s["coverage"])
    if worst["coverage"] >= 1:
        return "100%", "everything needed", False
    return pct(worst["coverage"]), f"of the {SUPPLY_PLAIN[worst['key']][1]} needed (lowest)", True


def _ward_page(w: dict, gen: str) -> str:
    b = w["beds"]
    rng = range_phrase(w["low_80"], w["high_80"])
    cover, cover_sub, cover_bad = _lowest_cover(w["supplies"])
    exp = "<1" if w["expected"] < 1 else f"{whole(w['expected'])}"
    figs = (_fig("Expected cases", exp, rng or "this week")
            + _fig("Supplies covered", cover, cover_sub, cover_bad)
            + (_fig("Regular beds", f"{b['demand']} / {b['capacity']}", "patients who may need a bed / regular beds",
                    b["net_deficit"] > 0) if b else "")
            + _fig("Spraying crews", f"{w['crews_assigned']} of {w['crews_needed']}", "assigned / needed",
                   w["crews_assigned"] < w["crews_needed"]))
    flag = ""
    if w["flag"]:
        flag = (f'<p class="flagline"><span class="flag">{w["flag"]}</span> {_e(FLAG_MEANING[w["flag"]].capitalize())}. '
                f'{_e(w["reasons"]["supplies"])}</p>')
    crews = ""
    if w["reasons"]["crews"]:
        crews = f'<p class="muted">Crews: {_e(w["reasons"]["crews"])}</p>'
    if w["sim"]:
        crews += (f'<p class="muted">The assigned spraying should prevent {_e(cases_phrase(w["sim"]["prevented_cases"]))} '
                  f'over the next 2 to 4 weeks.</p>')
    return (f'<section class="page">{_top("Dengue Watch · ward brief", f"{w["name"]}, {w["city"]}", w, _status(w["flag"], w["tier"], bool(b and b["net_deficit"])))}'
            f'<div class="inshort"><b>In short.</b> {_e(w["summary"])}</div>'
            f'<div class="figs">{figs}</div>'
            f'<h2>Supplies</h2>{_supply_table(w["supplies"])}{flag}'
            + (f'<h2>Hospital beds</h2><p>{_e(w["bed_story"])}</p>' if b else "")
            + crews
            + f'<h2>What to do</h2>{_todo(w["actions"])}'
            + _sign_and_foot(w, gen) + "</section>")


def _short_cell(w: dict) -> str:
    short = [s for s in w["supplies"] if s["deficit"]]
    return _e(", ".join(supply_amount(s["key"], s["deficit"], sms=True) for s in short)) if short else "&ndash;"


def _beds_cell(w: dict) -> str:
    b = w["beds"] or {}
    parts = [f"send {t['patients']} to {_e(t['to_name'])}" for t in b.get("transfers_out", [])]
    parts += [f"take {t['patients']} from {_e(t['from_name'])}" for t in b.get("transfers_in", [])]
    if b.get("net_deficit"):
        parts.append(f"<b>{b['net_deficit']} with no bed</b>")
    return "; ".join(parts) if parts else "regular beds enough"


def _ward_status(w: dict) -> str:
    if w["flag"]:
        return w["flag"]
    b = w["beds"] or {}
    if b.get("net_deficit"):
        return "BED SHORTAGE"
    if w["crews_assigned"] < w["crews_needed"]:
        return "NEEDS CREWS"
    return "OK"


def _city_page(c: dict, gen: str) -> str:
    b = c["beds"]
    cover, cover_sub, cover_bad = _lowest_cover(c["supplies"])
    figs = (_fig("Expected cases", f"{whole(c['expected'])}", f"across {c['n_wards']} wards")
            + _fig("Supplies covered", cover, cover_sub, cover_bad)
            + (_fig("Regular beds", f"{b['demand']} / {b['capacity']}", "patients who may need a bed / regular beds",
                    b["unresolved"] > 0) if b else "")
            + _fig("Spraying crews", f"{c['crews_assigned']} of {c['crews_needed']}", "assigned / needed",
                   c["crews_assigned"] < c["crews_needed"]))
    flags = []
    if c["n_critical_deficit"]:
        flags.append(f'<p class="flagline"><span class="flag">CRITICAL DEFICIT</span> in {plural(c["n_critical_deficit"], "ward")}: '
                     f'these high-risk wards will not get all the supplies they need.</p>')
    if c["n_warning"]:
        flags.append(f'<p class="flagline"><span class="flag">WARNING</span> in {plural(c["n_warning"], "ward")}: '
                     f'lower-risk wards that will not get everything they need.</p>')
    if flags:
        st = c["settings"]
        flags.append(f'<p class="muted">Stock this week covers {st["stock_pct"]}% of what {_e(pool_words(st))} need; '
                     f'it goes first to wards with more risk or crowding.</p>')
    top = urgent_wards(c, 8)
    more = len(urgent_wards(c)) - len(top)
    rows = "".join(
        f"<tr><td class=\"nw\"><b>{_e(w['name'])}</b></td><td>{_e(w['tier'])}</td>"
        f"<td class=\"r num\">{'&lt;1' if w['expected'] < 1 else whole(w['expected'])}</td>"
        f"<td>{_short_cell(w)}</td><td>{_beds_cell(w)}</td>"
        f"<td class=\"r num nw\">{w['crews_assigned']} of {w['crews_needed']}</td>"
        f"<td class=\"st\">{_e(_ward_status(w))}</td></tr>" for w in top)
    wards = ('<table class="wt"><thead><tr><th>Ward</th><th>Risk</th><th class="r">Cases</th><th>Short of</th><th>Beds</th>'
             f'<th class="r">Crews</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>'
             + (f'<p class="muted">{plural(more, "more ward")} also need action; see Dengue Watch for the full list.</p>' if more > 0 else "")
             ) if top else '<p>No ward needs extra action this week.</p>'
    tx = c["transfers"]
    tx_html = ('<ul class="tx">' + "".join(f"<li>{_e(t['from_name'])} &rarr; {_e(t['to_name'])}: {plural(t['patients'], 'patient')}</li>"
                                          for t in tx) + "</ul>") if tx else ""
    status = ('<div class="status s-crit">CRITICAL DEFICIT<small>' + plural(c["n_critical_deficit"], "ward") + '</small></div>'
              if c["n_critical_deficit"] else '<div class="status s-warn">WARNING<small>' + plural(c["n_warning"], "ward") + '</small></div>'
              if c["n_warning"] else '<div class="status s-ok">Covered<small>all wards</small></div>')
    return (f'<section class="page">{_top("Dengue Watch · city brief", c["city"], c, status)}'
            f'<div class="inshort"><b>In short.</b> {_e(c["summary"])}</div>'
            f'<div class="figs">{figs}</div>'
            f'<h2>Supplies for the whole city</h2>{_supply_table(c["supplies"])}{"".join(flags)}'
            f'<h2>Wards needing action</h2>{wards}'
            + (f'<h2>Hospital beds</h2><p>{_e(c["bed_story"])}</p>{tx_html}' if b else "")
            + f'<h2>What to do</h2>{_todo(c["actions"])}'
            + _sign_and_foot(c, gen) + "</section>")


def _method_page(c: dict, gen: str) -> str:
    st = c["settings"]
    k = st.get("rates") or {}
    rates = ""
    if all(k.get(x) for x in SUPPLY_ORDER):
        rates = (f" Each expected case needs about {k['saline']:.1f} saline bags, {k['kits']:.1f} test kits and "
                 f"{k['beds']:.1f} extra (surge) beds; these rates were learned from 2017 to 2022 hospital records.")
    floor = f" Every other ward first gets at least {st['floor_pct']}% of its need." if st["floor_pct"] else ""
    basis = ("a bad week (the upper end of the likely range)" if st["basis"] == "surge" else "the expected number of cases")
    if st["sim"]:
        crews = (f"Each spraying crew is assumed to remove about {st['sim']['effect_pct']}% of a ward's mosquito breeding "
                 "sites (standing water), up to half of them with several crews. The cases-prevented figures re-run the "
                 "forecast with fewer breeding sites. They are estimates, not results from the field.")
    else:
        crews = ("Wards need 1 spraying crew at medium risk, 2 at high and 3 at critical risk, plus 1 for the most "
                 "crowded wards of each city.")
    return f"""<section class="page method">{_top("Dengue Watch · page 2", "How these numbers were made", c, "")}
<h2>The forecast</h2>
<p>Dengue Watch predicts how many dengue cases each ward will have {c['horizon']} weeks ahead. It uses recent rainfall,
temperature and humidity, each ward's drainage, crowding and sanitation, and the time of year. It learned from 2017 to
2022 and was checked on 2024 and 2025, where it was usually within about 1 case per ward per week. The "likely" range
covers about 8 in 10 outcomes. This brief uses data up to {day(c['issue_date'])}.</p>
<h2>Supplies and stock</h2>
<p>Supply needs come straight from the expected cases.{rates} This brief assumes the regional store holds
{st['stock_pct']}% of what {_e(pool_words(st))} need. Critical-risk wards are served first, then the other wards in order
of risk and crowding, until the stock runs out.{floor} The {st['crews_total']} spraying crews in the pool are shared the
same way.</p>
<h2>Hospital beds</h2>
<p>The data has no official bed count, so each ward's regular beds are estimated as the most it needed in a bad week
(the busiest 1 week in 20) during 2017 to 2022, with at least 2 beds{'' if st['capacity_pct'] == 100 else f', then scaled to {st["capacity_pct"]}%'}.
Bed needs are planned for {basis}. Patients who cannot get a regular bed are sent to the ward in the same city with the
most free regular beds; wards at critical risk keep their spare beds. Distances are not known, so travel time is not considered.</p>
<h2>Spraying crews</h2>
<p>{_e(crews)}</p>
<h2>Limits</h2>
<p>Forecasts can miss sudden outbreaks or changes in how cases are reported. Supply, bed and crew figures are planning
estimates. Check local conditions before acting.</p>
<div class="spacer"></div>
<div class="foot"><span>Built from fixed templates on the Dengue Watch server, without internet or AI text generation.</span>
<span class="num">Generated {gen}</span></div>
</section>"""


def brief_html(c: dict, generated_at: datetime | None = None, method: bool | None = None) -> str:
    """Printable brief for a city context (or a one-ward context from city_context(only=...)).

    Page 1 is for decision makers; page 2 (method) is included by default for city briefs only.
    """
    gen = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    wards = c["wards"]
    is_ward = c["scope"] == "ward" and len(wards) == 1
    if method is None:
        method = not is_ward
    title = f"{wards[0]['name']}, {c['city']}" if is_ward else c["city"]
    page1 = _ward_page(wards[0], gen) if is_ward else _city_page(c, gen)
    page2 = _method_page(c, gen) if method else ""
    if not method:   # no page 2: point the footer at nothing that isn't there
        page1 = page1.replace(" Numbers are estimates; see page 2 for method.", " Numbers are estimates.")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dengue brief: {_e(title)}, week of {day(c['target_date'])}</title>
<style>{BRIEF_CSS}</style></head>
<body>
<div class="noprint"><button type="button" onclick="window.print()">Print / Save as PDF</button></div>
{page1}
{page2}
</body></html>
"""


# ---------------------------------------------------------------- one entry point
FORMATS = ("sms", "whatsapp", "brief", "json")
SCOPES = ("ward", "city")


def build(scope: str, ident: str, alloc: dict, overflow: dict, sim: dict | None = None,
          generated_at: datetime | None = None, method: bool | None = None) -> dict:
    """All three channels for one ward or one city, plus the structured context they came from."""
    if scope == "ward":
        ctx = ward_context(ident, alloc, overflow, sim)
        brief_ctx = city_context(ctx["city"], alloc, overflow, sim, only=[ident])
        sms, wa = sms_ward(ctx), whatsapp_ward(ctx)
    elif scope == "city":
        ctx = city_context(ident, alloc, overflow, sim)
        brief_ctx = ctx
        sms, wa = sms_city(ctx), whatsapp_city(ctx)
    else:
        raise ValueError(f"scope must be one of {SCOPES}")
    if method is None:
        method = scope == "city"
    stem = safe_name(ident)
    return {
        "scope": scope, "id": ident, "context": ctx, "method": bool(method),
        "sms": sms, "sms_info": sms_info(sms), "whatsapp": wa,
        "brief": brief_html(brief_ctx, generated_at, method),
        "files": {"sms": f"{stem}_sms.txt", "whatsapp": f"{stem}_whatsapp.txt", "brief": f"{stem}_brief.html"},
    }
