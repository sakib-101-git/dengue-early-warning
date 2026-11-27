import { api, isAbort } from "../api.js";
import { heatmapOption, makeChart } from "../charts.js";
import { getMeta, setState } from "../state.js";
import { cardHead, combobox, countUp, day, errorBox, esc, field, fmt, segmented, weekStepper } from "../ui.js";

const TIERS_DESC = ["Critical", "High", "Medium", "Low"];

export function mountCity(root, st) {
  const meta = getMeta();
  root.innerHTML = `
    <header class="pagehead"><div>
      <div class="crumbs"><span>City</span></div>
      <h1 data-title>&nbsp;</h1>
      <p data-sub>&nbsp;</p>
    </div></header>
    <section class="toolbar toolbar--city" aria-label="Forecast settings"></section>
    <section class="card" aria-label="Wards needing action">
      <div class="need-slot"><div class="skel skel--big" style="width:50%"></div><div class="skel skel--line" style="height:12px"></div></div>
    </section>
    <section class="card" aria-labelledby="rank-t">
      ${cardHead("Where to send teams first", "",
        "Expected cases in the forecast week, highest first; High and Critical risk wards need action. Select a ward to open it.").replace("<h2>", '<h2 id="rank-t">')}
      <div class="rank-slot">${'<div class="skel skel--line" style="height:22px;margin:14px 0"></div>'.repeat(6)}</div>
    </section>
    <section class="card" aria-labelledby="heat-t">
      <div class="card__head"><div><h2 id="heat-t">Risk through the year</h2></div>
        <div class="right"><div class="select-sm" data-year></div></div></div>
      <div class="chart" data-heat role="img"></div>
      <div class="legend" data-heat-legend></div>
    </section>`;

  const bar = root.querySelector(".toolbar");
  const cityBox = combobox({ label: "City", options: meta.cities.map((c) => ({ value: c.name, label: c.name, hint: `${c.wards.length} wards` })),
    value: st.city, onChange: (v) => setState({ city: v }) });
  const hSeg = segmented({ label: "Weeks ahead", items: meta.horizons.map((h) => ({ value: h, label: `${h} weeks` })), value: st.h,
    onChange: (v) => setState({ h: Number(v) }) });
  const stepper = weekStepper({ value: st.date, min: meta.min_date, max: meta.max_date, onChange: (v) => setState({ date: v, year: 0 }) });
  bar.append(field("City", cityBox.el), field("Weeks ahead", hSeg.el, { cls: "field--h" }),
             field("Data up to", stepper.el, { cls: "field--date", tip: "Forecasts use data up to this week." }));

  const years = [...meta.years].reverse();
  const yearBox = combobox({ label: "Year", options: years.map((y) => ({ value: y, label: String(y) })), value: years[0],
    onChange: (v) => setState({ year: Number(v) }, { push: false }) });
  root.querySelector("[data-year]").append(yearBox.el);
  root.querySelector("[data-heat-legend]").innerHTML = ["Low", "Medium", "High", "Critical"].map((t) =>
    `<span><i class="tier-${t}" style="background:${t === "Low" ? "color-mix(in srgb,var(--tier) 18%,var(--surface))" : "var(--tier)"}"></i>${t}</span>`).join("") +
    `<span><i class="line" style="background:var(--ink);width:2px;height:12px"></i>Selected week</span>`;

  const heat = makeChart(root.querySelector("[data-heat]"));
  let lastCity = "", lastHeat = "", loaded = false;

  async function loadCity(s) {
    const key = [s.city, s.date, s.h].join("|");
    if (key === lastCity) return null;
    lastCity = key;
    if (loaded) root.classList.add("is-busy");
    try {
      const d = await api("city", { city: s.city, date: s.date, h: s.h }, "city");
      renderCity(d);
      loaded = true;
      return d;
    } catch (e) {
      if (isAbort(e)) return null;
      lastCity = "";
      root.querySelector(".need-slot").replaceChildren(errorBox(e.message, () => loadCity(s)));
      return null;
    } finally { root.classList.remove("is-busy"); }
  }

  async function loadHeat(s, target) {
    const year = s.year || Number(target.slice(0, 4));
    yearBox.set(year);
    const key = [s.city, year, s.h, target].join("|");
    if (key === lastHeat) return;
    lastHeat = key;
    try {
      const hm = await api("heatmap", { city: s.city, year, h: s.h }, "heatmap");
      const el = root.querySelector("[data-heat]");
      el.style.height = `${48 + 26 * hm.wards.length}px`;
      el.setAttribute("aria-label", `Risk tier for each of ${hm.wards.length} wards in ${s.city}, every week of ${year}.`);
      heat.inst.resize();
      heat.render(heatmapOption(hm, target, el.clientWidth), { replace: true });
    } catch (e) {
      if (isAbort(e)) return;
      lastHeat = "";
    }
  }

  function renderCity(d) {
    root.querySelector("[data-title]").textContent = d.city;
    root.querySelector("[data-sub]").innerHTML =
      `Week of <b>${day(d.target_date, true)}</b>`;

    // headline + tier mix
    const slot = root.querySelector(".need-slot");
    if (!slot.querySelector("[data-need]")) {
      slot.innerHTML = `<div class="headline"><b class="fig"><span data-need>0</span><span> of <span data-all></span></span></b>
        <p>wards need action</p><strong data-by hidden></strong></div>
        <div class="mix" role="img" data-mix></div><div class="legend" data-mixlegend></div>`;
    }
    countUp(slot.querySelector("[data-need]"), d.need_action, { decimals: 0, duration: 800 });
    slot.querySelector("[data-all]").textContent = d.n_wards;
    slot.querySelector("[data-by]").textContent = day(d.target_date);
    const mix = slot.querySelector("[data-mix]");
    mix.setAttribute("aria-label", TIERS_DESC.map((t) => `${d.tier_counts[t]} ${t.toLowerCase()}`).join(", "));
    mix.innerHTML = TIERS_DESC.filter((t) => d.tier_counts[t]).map((t, i) =>
      `<div class="tier-${t}" style="flex:${d.tier_counts[t]};animation-delay:${i * 70}ms" title="${d.tier_counts[t]} ${t}"></div>`).join("");
    slot.querySelector("[data-mixlegend]").innerHTML = TIERS_DESC.map((t) =>
      `<span class="tier-${t}"><i style="background:var(--tier)"></i><b class="num" style="font-weight:500;color:var(--ink)">${d.tier_counts[t]}</b> ${t.toLowerCase()}</span>`).join("");

    // ranking, split into two balanced columns
    const top = Math.max(...d.wards.map((w) => w.expected), 1) * 1.05;
    const half = Math.ceil(d.wards.length / 2);
    const colHtml = (ws) => `<div class="rank-col" role="list">
      <div class="rank-head" aria-hidden="true"><span>#</span><span>Ward</span><span>Cases</span><span></span><span>Likely</span><span>Risk</span></div>
      ${ws.map((w, i) => `<button type="button" role="listitem" class="rank tier-${w.tier}" data-ward="${esc(w.ward)}"
          aria-label="${w.rank}. ${esc(w.label)}: ${fmt(w.expected)} cases expected, likely ${Math.round(w.low_80)} to ${Math.ceil(w.high_80)}, ${w.tier} risk. Open ward.">
        <span class="rank__n">${w.rank}</span><span class="rank__w">${esc(w.label)}</span>
        <span class="rank__lane"><span class="rank__fill" style="width:${(100 * w.expected) / top}%;animation-delay:${i * 22}ms"></span></span>
        <span class="rank__num">${fmt(w.expected)}</span><span class="rank__rng">${Math.round(w.low_80)}–${Math.ceil(w.high_80)}</span>
        <span class="rank__t">${w.tier}</span></button>`).join("")}</div>`;
    root.querySelector(".rank-slot").innerHTML = `<div class="ranks">${colHtml(d.wards.slice(0, half))}${colHtml(d.wards.slice(half))}</div>`;
  }

  root.querySelector(".rank-slot").addEventListener("click", (e) => {
    const b = e.target.closest("[data-ward]");
    if (b) setState({ view: "ward", ward: b.dataset.ward, rain: 0, temp: 0, hum: 0 });
  });

  return {
    async update(s, prev) {
      cityBox.set(s.city); hSeg.set(s.h); stepper.set(s.date);
      const target = (await loadCity(s))?.target_date || root._target;
      if (target) root._target = target;
      if (root._target) loadHeat(s, root._target);
    },
    destroy() { heat.dispose(); },
  };
}
