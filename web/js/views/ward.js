import { api, isAbort } from "../api.js";
import { makeChart, seasonOption } from "../charts.js";
import { getMeta, setState, wardsOf } from "../state.js";
import {
  cardHead, combobox, countUp, day, debounce, errorBox, esc, field, fmt, segmented, signedPct, simPopover, weekStepper,
} from "../ui.js";

const TOP_DRIVERS = 5;

export function mountWard(root, st) {
  const meta = getMeta();
  root.innerHTML = `
    <header class="pagehead"><div>
      <div class="crumbs"><span data-city></span><i></i><span>Ward forecast</span></div>
      <h1 data-title>&nbsp;</h1>
      <p data-sub>&nbsp;</p>
    </div></header>
    <section class="toolbar" aria-label="Forecast settings"></section>
    <section class="card hero" aria-labelledby="hero-t">
      ${cardHead("Rain now, cases later", "Weekly rain hangs from the sky line. Rain in the breeding window drives the forecast.",
        "Mosquitoes breed in standing water after rain. Eggs become biting adults in 1–2 weeks, then the virus incubates, so rain shows up in hospital cases 2–4 weeks later.").replace("<h2>", '<h2 id="hero-t">')}
      <div class="strip-slot"><div class="skel skel--block" style="height:300px"></div></div>
      <div class="legend strip-legend">
        <span><i style="background:var(--accent)"></i>Rain in the breeding window</span>
        <span><i style="background:var(--rain-soft)"></i>Other rain</span>
        <span><i class="hatch"></i>Weeks not known yet</span>
        <span class="legend-tier"><i data-tier-swatch></i>Forecast week</span>
      </div>
    </section>
    <section class="card summary span-5" aria-label="Risk summary"><div class="sum-slot">
      <div class="skel skel--line" style="width:90px;height:26px;border-radius:99px"></div><div class="skel skel--big"></div>
      <div class="skel skel--line" style="width:80%"></div><div class="skel skel--line" style="width:100%;margin-top:40px"></div>
      <div class="skel skel--line"></div><div class="skel skel--line"></div></div>
    </section>
    <section class="card span-7" aria-labelledby="drv-t">
      ${cardHead("What pushes the risk", "Effect of each factor on expected cases this week").replace("<h2>", '<h2 id="drv-t">')}
      <div class="drv-slot">${'<div class="skel skel--line" style="height:20px;margin:14px 0"></div>'.repeat(5)}</div>
    </section>
    <section class="card" aria-labelledby="season-t">
      ${cardHead("", "Forecast and recorded cases each week, with the likely range").replace("<h2>", '<h2 id="season-t" data-season-title>')}
      <div class="chart chart--season" data-season role="img"></div>
      <div class="legend">
        <span><i class="line" style="background:var(--accent)"></i>Forecast</span>
        <span><i style="background:color-mix(in srgb,var(--accent) 22%,transparent)"></i>Likely range (8 in 10 weeks)</span>
        <span><i class="dot" style="background:var(--ink)"></i>Recorded cases</span>
        <span><i class="dot" data-tier-swatch></i>This forecast</span>
      </div>
    </section>`;

  // ---------------------------------------------------------------- controls
  const bar = root.querySelector(".toolbar");
  const cityBox = combobox({ label: "City", options: meta.cities.map((c) => ({ value: c.name, label: c.name, hint: `${c.wards.length} wards` })),
    value: st.city, onChange: (v) => setState({ city: v }) });
  const wardOpts = (city) => wardsOf(city).map((w) => ({ value: w.id, label: w.label }));
  const wardBox = combobox({ label: "Ward", options: wardOpts(st.city), value: st.ward, search: true, placeholder: "Find a ward",
    onChange: (v) => setState({ ward: v }) });
  const hSeg = segmented({ label: "Weeks ahead", items: meta.horizons.map((h) => ({ value: h, label: `${h} weeks` })), value: st.h,
    onChange: (v) => setState({ h: Number(v) }) });
  const stepper = weekStepper({ value: st.date, min: meta.min_date, max: meta.max_date, onChange: (v) => setState({ date: v }) });
  const pushSim = debounce((vals) => setState(vals, { push: false }), 150);
  const sim = simPopover({ values: st, onInput: pushSim, onReset: () => setState({ rain: 0, temp: 0, hum: 0 }) });
  bar.append(
    field("City", cityBox.el),
    field("Ward", wardBox.el),
    field("Weeks ahead", hSeg.el, { cls: "field--h", tip: "How far ahead to warn. Rain needs 2–4 weeks to turn into cases." }),
    field("Data up to", stepper.el, { cls: "field--date", tip: "The forecast only uses weather up to this week." }),
    field("What if?", sim.el, { cls: "field--sim" }),
  );

  const season = makeChart(root.querySelector("[data-season]"));
  let lastKey = "";
  let loaded = false;

  async function load(s) {
    const key = [s.ward, s.date, s.h, s.rain, s.temp, s.hum].join("|");
    if (key === lastKey) return;
    lastKey = key;
    if (loaded) root.classList.add("is-busy");
    root.setAttribute("aria-busy", "true");
    try {
      const d = await api("ward", { ward: s.ward, date: s.date, h: s.h, rain: s.rain, temp: s.temp, hum: s.hum }, "ward");
      render(d, s);
      loaded = true;
    } catch (e) {
      if (isAbort(e)) return;
      lastKey = "";
      const slot = root.querySelector(".strip-slot");
      slot.replaceChildren(errorBox(e.message, () => load(s)));
    } finally {
      if (lastKey === key || !lastKey) { root.classList.remove("is-busy"); root.removeAttribute("aria-busy"); }
    }
  }

  function render(d, s) {
    const f = d.forecast;
    root.querySelector("[data-city]").textContent = f.city;
    root.querySelector("[data-title]").textContent = f.ward_label;
    root.querySelector("[data-sub]").innerHTML =
      `Forecast for the week of <b>${day(f.target_date, true)}</b>, ${f.horizon} weeks ahead`;
    root.querySelectorAll("[data-tier-swatch]").forEach((el) => { el.className = el.className.replace(/\btier-\w+/g, "").trim(); el.classList.add(`tier-${f.tier}`); el.style.background = "var(--tier)"; });
    renderStrip(root.querySelector(".strip-slot"), d);
    renderSummary(root.querySelector(".sum-slot"), d);
    renderDrivers(root.querySelector(".drv-slot"), d);
    root.querySelector("[data-season-title]").textContent = `${f.target_date.slice(0, 4)} season`;
    const sEl = root.querySelector("[data-season]");
    const peak = d.season.reduce((a, b) => (b.expected > a.expected ? b : a), d.season[0]);
    sEl.setAttribute("aria-label", `Weekly forecast for ${f.ward_label} in ${f.target_date.slice(0, 4)}. ` +
      `Peak forecast ${fmt(peak.expected)} cases in the week of ${day(peak.date)}. This forecast: ${fmt(f.expected)} cases.`);
    season.render(seasonOption(d.season, f.target_date, f.tier));
  }

  return {
    update(s, prev) {
      if (!prev || s.city !== prev.city) wardBox.set(s.ward, wardOpts(s.city));
      cityBox.set(s.city); wardBox.set(s.ward); hSeg.set(s.h); stepper.set(s.date); sim.set(s);
      load(s);
    },
    destroy() { season.dispose(); },
  };
}

// ---------------------------------------------------------------- the rain -> risk hero strip
function niceMax(v) {
  const step = v > 300 ? 100 : 50;
  return Math.max(100, Math.ceil((v * 1.12) / step) * step);
}

function renderStrip(slot, d) {
  const f = d.forecast;
  const rain = d.rain, ahead = d.ahead;
  const max = niceMax(Math.max(...rain.map((r) => r.mm), 0));
  const step = max > 300 ? 100 : 50;
  const n = rain.length, w = ahead.length;
  const structure = `${rain[0]?.date}|${n}|${w}`;
  const existing = slot.querySelector(".strip");

  // Same weeks, new numbers (e.g. simulation): morph heights instead of replaying the fall.
  if (existing && existing.dataset.structure === structure) {
    existing.querySelectorAll(".drop").forEach((el, i) => {
      el.style.height = `${(100 * rain[i].mm) / max}%`;
      const v = el.parentElement.querySelector(".drop__val");
      if (v) { v.textContent = Math.round(rain[i].mm); v.style.top = `calc(${(100 * rain[i].mm) / max}% + 8px)`; }
      const tip = el.parentElement.querySelector(".tip"); if (tip) tip.textContent = `${day(rain[i].date)} · ${Math.round(rain[i].mm)} mm`;
    });
    existing.style.setProperty("--max", max);
    retarget(existing, f);
    existing.setAttribute("aria-label", stripLabel(d));
    const scale = existing.querySelector(".strip__scale");
    scale.innerHTML = scaleMarks(max, step);
    existing.querySelector(".strip__guides").innerHTML = guideMarks(max, step);
    return;
  }

  const colOf = (i) => 2 + i;                     // grid column for rain week i (col 1 = scale)
  const waitCol = (j) => 2 + n + j;
  const targetCol = 2 + n + w;
  // breeding window span (rain weeks + not-yet-known weeks)
  const hot = [...rain.map((r, i) => (r.breeding ? colOf(i) : null)), ...ahead.map((a, j) => (a.breeding ? waitCol(j) : null))].filter(Boolean);
  const waitOnly = ahead.map((a, j) => (!a.breeding ? waitCol(j) : null)).filter(Boolean);

  const cols = rain.map((r, i) => {
    const showVal = r.breeding && r.mm >= 1;
    return `<div class="col" style="grid-column:${colOf(i)}" aria-hidden="true">
      <div class="drop${r.breeding ? " is-hot" : ""}" style="height:${(100 * r.mm) / max}%;animation-delay:${i * 40}ms"></div>
      ${showVal ? `<span class="drop__val${r.breeding ? " is-hot" : ""}" style="top:calc(${(100 * r.mm) / max}% + 8px)">${Math.round(r.mm)}</span>` : ""}
      <span class="tip">${day(r.date)} · ${Math.round(r.mm)} mm</span></div>`;
  }).join("");
  const waits = ahead.map((a, j) => `<div class="wait${a.breeding ? " is-hot" : ""}" style="grid-column:${waitCol(j)};animation-delay:${n * 40 + j * 90}ms" aria-hidden="true" title="Week of ${day(a.date)}: weather not known yet"></div>`).join("");
  const ticks = rain.map((r, i) => {
    const isIssue = i === n - 1;
    const show = isIssue || (n - 1 - i) % 4 === 0;
    return `<div class="tick${isIssue ? " is-strong" : ""}" style="grid-column:${colOf(i)}">${show ? day(r.date) : ""}</div>`;
  }).join("") + `<div class="tick is-strong" style="grid-column:${targetCol}">${day(f.target_date)}</div>`;
  const brackets =
    (hot.length ? `<div class="bracket" style="grid-column:${Math.min(...hot)} / ${Math.max(...hot) + 1}"><span class="long">Breeding window</span><span class="short">Breeding</span></div>` : "") +
    (waitOnly.length ? `<div class="bracket bracket--wait" style="grid-column:${Math.min(...waitOnly)} / ${Math.max(...waitOnly) + 1}">Wait</div>` : "") +
    `<div class="bracket bracket--target tier-${f.tier}" data-tbr style="grid-column:${targetCol}"><span class="long">Forecast week</span><span class="short">Forecast</span></div>`;

  slot.innerHTML = `
    <div class="strip" role="img" data-structure="${esc(structure)}" style="--cols:${n};--wait:${w}" aria-label="${esc(stripLabel(d))}">
      <div class="strip__scale" aria-hidden="true">${scaleMarks(max, step)}</div>
      <div class="strip__guides" aria-hidden="true">${guideMarks(max, step)}</div>
      <div class="strip__sky" aria-hidden="true"></div>
      ${cols}${waits}
      <div class="target tier-${f.tier}" style="grid-column:${targetCol}">
        <span class="target__tier tier-${f.tier}" data-ttier>${f.tier} risk</span>
        <div><b class="fig" data-tnum>0.0</b><small data-trng></small></div>
      </div>
      ${ticks}${brackets}
    </div>`;
  retarget(slot.querySelector(".strip"), f, true);
}

function scaleMarks(max, step) {
  let out = "";
  for (let v = step; v <= max; v += step) out += `<span style="top:${(100 * v) / max}%">${v} mm</span>`;
  return out;
}
function guideMarks(max, step) {
  let out = "";
  for (let v = step; v <= max; v += step) out += `<i style="top:${(100 * v) / max}%"></i>`;
  return out;
}

function retarget(strip, f, fresh = false) {
  const t = strip.querySelector(".target");
  t.className = `target tier-${f.tier}`;
  const tt = strip.querySelector("[data-ttier]");
  tt.className = `target__tier tier-${f.tier}`;
  tt.textContent = `${f.tier} risk`;
  strip.querySelector("[data-tbr]").className = `bracket bracket--target tier-${f.tier}`;
  strip.querySelector("[data-trng]").innerHTML =
    `cases expected<span class="long"> · likely ${Math.round(f.low_80)}–${Math.round(f.high_80)}</span>`;
  const num = strip.querySelector("[data-tnum]");
  if (fresh) setTimeout(() => countUp(num, f.expected, { decimals: 1, duration: 1000 }), 420);
  else countUp(num, f.expected, { decimals: 1, duration: 500 });
}

function stripLabel(d) {
  const f = d.forecast;
  const hot = d.rain.filter((r) => r.breeding);
  const hotMm = hot.reduce((a, r) => a + r.mm, 0);
  return `Rain for the 12 weeks to ${day(f.issue_date, true)}. ${Math.round(hotMm)} mm fell in the breeding window ` +
    `(2 to 4 weeks before the forecast week). Forecast for the week of ${day(f.target_date, true)}: ` +
    `${f.tier} risk, ${fmt(f.expected)} cases expected.`;
}

// ---------------------------------------------------------------- risk summary
function renderSummary(slot, d) {
  const f = d.forecast, b = d.baseline;
  if (!slot.querySelector("[data-big]")) {
    slot.innerHTML = `
      <div class="summary__top"><span class="tier" data-tier></span><span class="chip" data-simchip hidden>Simulated weather</span></div>
      <div class="big fig" data-big aria-live="polite">0.0</div>
      <p class="big-sub" data-bigsub></p>
      <p class="delta" data-delta hidden></p>
      <dl class="kv">
        <dt>Likely range</dt><dd data-range></dd>
        <dt>Recorded</dt><dd data-actual></dd>
        <dt>Warning lead time</dt><dd data-lead></dd>
      </dl>`;
  }
  const tier = slot.querySelector("[data-tier]");
  tier.className = `tier tier-${f.tier}`;
  tier.textContent = `${f.tier} risk`;
  slot.querySelector("[data-simchip]").hidden = !d.simulated;
  countUp(slot.querySelector("[data-big]"), f.expected, { decimals: 1, duration: 900 });
  slot.querySelector("[data-bigsub]").innerHTML =
    `cases expected in <b>${esc(f.ward_label)}, ${esc(f.city)}</b>, week of ${day(f.target_date, true)}`;
  const delta = slot.querySelector("[data-delta]");
  delta.hidden = !d.simulated;
  if (d.simulated) {
    const dv = f.expected - b.expected;
    delta.textContent = `${dv >= 0 ? "+" : "−"}${Math.abs(dv).toFixed(1)} cases vs. the weather that actually happened (${fmt(b.expected)}, ${b.tier.toLowerCase()} risk)`;
  }
  slot.querySelector("[data-range]").textContent = `${Math.round(f.low_80)}–${Math.round(f.high_80)} cases`;
  const act = slot.querySelector("[data-actual]");
  act.textContent = f.actual === null ? "Not yet recorded" : `${f.actual} cases`;
  act.classList.toggle("muted", f.actual === null);
  slot.querySelector("[data-lead]").textContent = `${f.horizon} weeks · issued ${day(f.issue_date)}`;
}

// ---------------------------------------------------------------- drivers (diverging bars)
function renderDrivers(slot, d) {
  const rows = d.drivers.filter((x) => Math.abs(x.log_effect) > 0.01).slice(0, TOP_DRIVERS);
  if (!rows.length) { slot.innerHTML = `<p class="card__head"><span style="color:var(--ink-3)">No single factor stands out this week.</span></p>`; return; }
  const m = Math.max(...rows.map((r) => Math.abs(r.log_effect)));
  slot.innerHTML = `<div class="drivers" role="list" aria-label="Factors that change expected cases">
    <div class="drv-axis" aria-hidden="true"><span></span><div><span>Lowers risk</span><span>Raises risk</span></div><span></span></div>
    ${rows.map((r, i) => {
      const w = (50 * Math.abs(r.log_effect)) / m;
      const up = r.log_effect > 0;
      return `<div class="drv" role="listitem" aria-label="${esc(r.label)}: ${signedPct(r.pct_effect)} ${up ? "more" : "fewer"} expected cases">
        <span>${esc(r.label)}</span>
        <div class="drv__track"><div class="drv__bar ${up ? "up" : "down"}" style="left:${up ? 50 : 50 - w}%;width:${w}%;animation-delay:${i * 60}ms"></div></div>
        <span class="drv__val ${up ? "up" : "down"}">${signedPct(r.pct_effect)}</span></div>`;
    }).join("")}</div>
    <p class="note">Compared with a typical ward-week (${fmt(d.typical_cases)} cases). Factors multiply, so percentages don't add up.</p>`;
}
