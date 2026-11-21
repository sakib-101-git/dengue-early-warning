"""Small HTML building blocks rendered with st.markdown(unsafe_allow_html=True)."""
from __future__ import annotations

from html import escape

import numpy as np
import pandas as pd

from app.theme import INK_3, LOWER, RAISE, RAIN, RAIN_SOFT, TIER


def day_mon(d, year: bool = False) -> str:
    """'7 Jul' / '7 Jul 2025' without the non-portable %-d (breaks on Windows)."""
    d = pd.Timestamp(d)
    return f"{d.day} {d:%b}" + (f" {d.year}" if year else "")

PLAIN_DRIVER = {
    "Rain 2-4 weeks before target week (breeding window)": "Rain 2–4 weeks before",
    "Rain 5+ weeks before target week": "Earlier rain (5+ weeks)",
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

INFO_ICON = ("<svg width='15' height='15' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' "
             "stroke-linecap='round' aria-hidden='true'><circle cx='12' cy='12' r='9.5'/><path d='M12 11v5.5'/>"
             "<circle cx='12' cy='7.6' r='.6' fill='currentColor'/></svg>")


def short_ward(ward_id: str) -> str:
    return "Ward " + ward_id.rsplit("_", 1)[-1]


# ---------------------------------------------------------------- headings
def info(text: str) -> str:
    """An (i) icon with a tooltip that also opens on keyboard focus."""
    return (f"<span class='dw-info' tabindex='0' aria-label='{escape(text)}'>{INFO_ICON}"
            f"<span role='tooltip'>{escape(text)}</span></span>")


def page_title(title: str, sub: str, right: str = "") -> str:
    return f"<div class='dw-title'><div><h1 class='dw-serif'>{escape(title)}</h1><p class='dw-mono'>{escape(sub)}</p></div>{right}</div>"


def head(title: str, sub: str = "", tip: str = "") -> str:
    """Panel header: title, optional caption, optional info tooltip on the right."""
    sub_html = f"<p>{escape(sub)}</p>" if sub else ""
    return f"<div class='dw-head'><div><h3>{escape(title)}</h3>{sub_html}</div>{info(tip) if tip else ''}</div>"


def tier_pill(tier: str) -> str:
    t = TIER[tier]
    return f"<span class='dw-tier' style='background:{t['soft']};color:{t['text']}'>{tier} risk</span>"


def chip(text: str) -> str:
    return f"<span class='dw-chip'>{escape(text)}</span>"


# ---------------------------------------------------------------- ward page
def rain_legend(tier: str, has_gap: bool) -> str:
    t = TIER[tier]
    gap = ("<span><i style='background:rgba(24,24,27,.07);box-shadow:inset 0 0 0 1px #E4E4E7'></i>"
           "Not known yet</span>") if has_gap else ""
    return ("<div class='dw-legend'>"
            f"<span><i style='background:{RAIN}'></i>Rain in the breeding window</span>"
            f"<span><i style='background:{RAIN_SOFT}'></i>Other rain</span>{gap}"
            f"<span><i class='dot' style='background:{t['bg']}'></i>Forecast week</span></div>")


def summary(fc, ward: str, h: int, issue, recorded: str, sim_note: str = "") -> str:
    """Risk summary: tier, expected cases, likely range, recorded, warning lead time."""
    sim = f"<p class='dw-sim'>{escape(sim_note)}</p>" if sim_note else ""
    badges = tier_pill(fc.tier) + (chip("Simulated") if sim_note else "")
    return (
        f"<div class='dw-sum-top'>{badges}</div>"
        f"<div class='dw-big dw-serif'>{fc.expected_cases:.1f}</div>"
        f"<div class='dw-big-sub'>cases expected in {escape(short_ward(ward))}, week of {day_mon(fc.target_date, True)}</div>"
        f"<dl class='dw-kv'><dt class='dw-mono'>Likely range</dt><dd class='dw-mono'>{fc.low_80:.0f}–{fc.high_80:.0f} cases</dd>"
        f"<dt class='dw-mono'>Recorded</dt><dd class='dw-mono'>{escape(recorded)}</dd>"
        f"<dt class='dw-mono'>Lead time</dt><dd class='dw-mono'>{h} weeks, issued {day_mon(issue)}</dd></dl>{sim}"
    )


def driver_rows(grouped: pd.DataFrame, top: int = 5) -> str:
    g = grouped[grouped.log_effect.abs() > 0.01].head(top)
    if g.empty:
        return "<p class='dw-caption'>No single factor stands out this week.</p>"
    m = g.log_effect.abs().max()
    rows = []
    for i, r in enumerate(g.itertuples()):
        w = 50 * abs(r.log_effect) / m
        up = r.log_effect > 0
        left = 50 if up else 50 - w
        rows.append(
            f"<div class='dw-drv'><span>{escape(PLAIN_DRIVER.get(r.group, r.group))}</span>"
            f"<div class='track'><div class='bar {'up' if up else 'down'}' style='left:{left:.1f}%;width:{w:.1f}%;"
            f"background:{RAISE if up else LOWER};animation-delay:{i * 60}ms'></div></div>"
            f"<span class='val'>{r.pct_effect:+.0f}%</span></div>"
        )
    legend = (f"<div class='dw-legend' style='margin-top:14px'><span><i style='background:{RAISE}'></i>Raises risk</span>"
              f"<span><i style='background:{LOWER}'></i>Lowers risk</span></div>")
    return "<div class='dw-drvs'>" + "".join(rows) + "</div>" + legend


def season_legend() -> str:
    return ("<div class='dw-legend'>"
            f"<span><i class='line' style='background:{RAIN}'></i>Forecast</span>"
            "<span><i style='background:rgba(11,106,128,.16)'></i>Likely range</span>"
            "<span><i class='dot' style='background:#0F172A'></i>Recorded</span>"
            f"<span><i class='line' style='background:repeating-linear-gradient(90deg,{INK_3} 0 3px,transparent 3px 5px)'></i>"
            "This forecast</span></div>")


# ---------------------------------------------------------------- city page
def need_action(n_hot: int, n_all: int, target) -> str:
    return (f"<div class='dw-need'><b class='dw-serif'>{n_hot} of {n_all}</b>"
            f"<span>wards need action before {day_mon(target)}</span></div>")


def tier_mix(counts: pd.Series) -> str:
    total = max(int(counts.sum()), 1)
    segs, legend = [], []
    for i, tier in enumerate(("Critical", "High", "Medium", "Low")):
        n = int(counts.get(tier, 0))
        if n:
            segs.append(f"<div style='flex:{n};background:{TIER[tier]['bg']};animation-delay:{i * 60}ms' "
                        f"title='{n} {tier}'></div>")
        legend.append(f"<span><i style='background:{TIER[tier]['bg']}'></i>{n} {tier.lower()}</span>")
    return (f"<div class='dw-mix' role='img' aria-label='{total} wards by risk'>{''.join(segs)}</div>"
            f"<div class='dw-legend'>{''.join(legend)}</div>")


def tier_legend(colors: dict | None = None) -> str:
    colors = colors or {t: v["bg"] for t, v in TIER.items()}
    items = "".join(f"<span><i style='background:{colors[t]}'></i>{t}</span>"
                    for t in ("Low", "Medium", "High", "Critical"))
    return f"<div class='dw-legend'>{items}</div>"


def ranking(rows: pd.DataFrame) -> str:
    top = max(float(rows.expected_cases.max()) * 1.08, 1.0)
    out = ["<div class='dw-ranks'>"]
    for i, r in enumerate(rows.itertuples()):
        t = TIER[r.tier]
        out.append(
            f"<div class='dw-rank' title='{escape(r.ward_id)}: {r.tier}'>"
            f"<span class='n'>{i + 1}</span><span class='w'>{escape(short_ward(r.ward_id))}</span>"
            f"<div class='lane'><div class='fill' style='width:{100 * r.expected_cases / top:.1f}%;"
            f"background:{t['bg']};animation-delay:{i * 25}ms'></div></div>"
            f"<span class='num'>{r.expected_cases:.1f}</span>"
            f"<span class='rng'>{r.low_80:.0f}–{np.ceil(r.high_80):.0f}</span>"
            f"<span class='t' style='color:{t['text']}'>{r.tier}</span></div>"
        )
    out.append("</div>")
    return "".join(out)


# ---------------------------------------------------------------- model page
def stats(items: list[tuple[str, str]], highlight: int | None = None) -> str:
    cells = "".join(f"<div><b class='dw-serif{' brand' if i == highlight else ''}'>{escape(v)}</b><span>{escape(k)}</span></div>"
                    for i, (v, k) in enumerate(items))
    return f"<div class='dw-stats'>{cells}</div>"


def steps(items: list[tuple[str, str]]) -> str:
    return "<div class='dw-steps'>" + "".join(
        f"<div><b>{escape(a)}</b>{escape(b)}</div>" for a, b in items) + "</div>"


BRAND = (
    "<div class='dw-brand'><div class='mark'>"
    "<svg width='16' height='16' viewBox='0 0 24 24' aria-hidden='true'>"
    "<path d='M12 2.8c3.1 4.1 6.3 7.8 6.3 11.3a6.3 6.3 0 0 1-12.6 0c0-3.5 3.2-7.2 6.3-11.3z' fill='#fff'/>"
    "<path d='M9.2 14.6a2.9 2.9 0 0 0 2.9 2.9' stroke='#0B6A80' stroke-width='1.7' fill='none' stroke-linecap='round'/>"
    "</svg></div><b>Dengue Watch</b><span>Ward early warning</span></div>"
)

__all__ = ["BRAND", "INK_3", "chip", "day_mon", "driver_rows", "head", "info", "need_action", "page_title",
           "rain_legend", "ranking", "season_legend", "short_ward", "stats", "steps", "summary", "tier_legend",
           "tier_mix", "tier_pill"]
