// Resources: constrained allocation of saline, diagnostic kits, beds and spraying crews.
import { api, isAbort } from "../api.js";
import { getMeta, setState } from "../state.js";
import { cardHead, combobox, day, debounce, errorBox, esc, field, fmt, info, segmented, weekStepper } from "../ui.js";

const n = (v) => Number(v).toLocaleString("en-US");
const pc = (v) => `${Math.round(v * 100)}%`;
const FLAG_CLS = { "CRITICAL DEFICIT": "flag--crit", WARNING: "flag--warn" };
const STOCK = { min: 30, max: 100, step: 5 };

export function mountResources(root, st) {
  const meta = getMeta();
  root.innerHTML = `
    <header class="pagehead"><div>
      <div class="crumbs"><span>Resource allocation</span></div>
      <h1 data-title>&nbsp;</h1>
      <p data-sub>&nbsp;</p>
    </div></header>
    <section class="toolbar toolbar--city" aria-label="Forecast settings"></section>
    <section class="card res-controls" aria-labelledby="sc-t">
      ${cardHead("Scarcity controls", "Drag to see who goes short. The plan re-runs live.",
        "Available stock is a share of what the wards in scope need this week. Crews are assigned down the same priority list.").replace("<h2>", '<h2 id="sc-t">')}
      <div class="res-controls__grid">
        <div class="slider res-slider">
          <div class="slider__top"><label for="res-stock">Available regional stock</label><output for="res-stock" class="is-on"></output></div>
          <input id="res-stock" type="range" min="${STOCK.min}" max="${STOCK.max}" step="${STOCK.step}">
          <div class="slider__ends" aria-hidden="true"><span>30%</span><span>100%</span></div>
        </div>
        <div class="field res-crews">
          <label for="res-crews">Total spraying crews</label>
          <div class="numstep">
            <button type="button" class="btn btn--sm" data-step="-1" aria-label="One crew fewer">−</button>
            <input id="res-crews" type="number" inputmode="numeric" min="0" max="1000" step="1">
            <button type="button" class="btn btn--sm" data-step="1" aria-label="One crew more">+</button>
          </div>
          <small class="res-hint" data-crew-hint>&nbsp;</small>
        </div>
        <div class="field res-floor" data-floor></div>
      </div>
    </section>
    <section class="res-stats" aria-label="Supply totals" data-stats>
      ${'<div class="card"><div class="skel skel--line" style="width:40%"></div><div class="skel skel--big" style="width:70%"></div></div>'.repeat(4)}
    </section>
    <section class="card" aria-labelledby="al-t" data-alerts-card>
      <div class="card__head"><div><h2 id="al-t">Deficit alerts</h2><p data-alerts-sub>&nbsp;</p></div></div>
      <div data-alerts></div>
    </section>
    <section class="card" aria-labelledby="tb-t">
      <div class="card__head"><div><h2 id="tb-t">Allocation plan by priority</h2>
        <p>Wards are served from the top of the list down. Select a ward to open its forecast.</p></div>
        <div class="right"><span class="chip">Priority rule</span><span data-rule></span></div></div>
      <div class="res-table-wrap" data-table>${'<div class="skel skel--line" style="height:22px;margin:14px 0"></div>'.repeat(8)}</div>
      <p class="res-foot" data-rates></p>
    </section>`;

  // ---- toolbar
  const bar = root.querySelector(".toolbar");
  const scopeOpts = [{ value: "all", label: "All cities", hint: "60 wards" },
    ...meta.cities.map((c) => ({ value: c.name, label: c.name, hint: `${c.wards.length} wards` }))];
  const scopeBox = combobox({ label: "Scope", options: scopeOpts, value: st.scope, onChange: (v) => setState({ scope: v }) });
  const hSeg = segmented({ label: "Weeks ahead", items: meta.horizons.map((h) => ({ value: h, label: `${h} weeks` })), value: st.h,
    onChange: (v) => setState({ h: Number(v) }) });
  const stepper = weekStepper({ value: st.date, min: meta.min_date, max: meta.max_date, onChange: (v) => setState({ date: v }) });
  bar.append(field("Scope", scopeBox.el), field("Weeks ahead", hSeg.el, { cls: "field--h" }),
             field("Data up to", stepper.el, { cls: "field--date", tip: "Supplies are planned for the forecast week: data-up-to week + weeks ahead." }));

  // ---- scarcity controls
  const stock = root.querySelector("#res-stock");
  const stockOut = root.querySelector('output[for="res-stock"]');
  const paintStock = (v) => {
    stock.value = v;
    stock.style.setProperty("--p", `${((v - STOCK.min) / (STOCK.max - STOCK.min)) * 100}%`);
    stock.style.setProperty("--z", "0%");
    stockOut.textContent = `${v}%`;
    stock.setAttribute("aria-valuetext", `${v}% of demand`);
  };
  const pushStock = debounce((v) => setState({ stock: v }, { push: false }), 160);
  stock.addEventListener("input", () => { paintStock(Number(stock.value)); pushStock(Number(stock.value)); });

  const crews = root.querySelector("#res-crews");
  const clampCrews = (v) => Math.min(1000, Math.max(0, Math.round(Number(v) || 0)));
  const pushCrews = debounce((v) => setState({ crews: v }, { push: false }), 250);
  crews.addEventListener("input", () => { if (crews.value !== "") pushCrews(clampCrews(crews.value)); });
  crews.addEventListener("change", () => { crews.value = clampCrews(crews.value); pushCrews(Number(crews.value)); });
  root.querySelector(".numstep").addEventListener("click", (e) => {
    const b = e.target.closest("[data-step]"); if (!b) return;
    crews.value = clampCrews(Number(crews.value) + Number(b.dataset.step) * (e.shiftKey ? 5 : 1));
    pushCrews(Number(crews.value));
  });

  const floorSeg = segmented({ label: "Minimum coverage floor", items: [{ value: 0, label: "Off" }, { value: 20, label: "20% for every ward" }],
    value: st.floor, onChange: (v) => setState({ floor: Number(v) }, { push: false }) });
  root.querySelector("[data-floor]").append(
    Object.assign(document.createElement("div"), { className: "label", innerHTML: `Minimum coverage floor${info("Off: pure priority fill. 20%: after Critical wards are filled, every other ward gets at least 20% of its need before the rest goes down the list.")}` }),
    floorSeg.el);

  // ---- data
  let lastKey = "", loaded = false;
  async function load(s) {
    const key = [s.scope, s.date, s.h, s.stock, s.crews, s.floor].join("|");
    if (key === lastKey) return;
    lastKey = key;
    if (loaded) root.classList.add("is-busy");
    try {
      const d = await api("allocation", { scope: s.scope, date: s.date, h: s.h, stock_pct: s.stock, crews: s.crews, floor: s.floor }, "allocation");
      render(d);
      loaded = true;
    } catch (e) {
      if (isAbort(e)) return;
      lastKey = "";
      root.querySelector("[data-stats]").replaceChildren(errorBox(e.message, () => load(s)));
    } finally { root.classList.remove("is-busy"); }
  }

  function render(d) {
    root.querySelector("[data-title]").textContent = `Supplies · ${d.scope_label}`;
    root.querySelector("[data-sub]").innerHTML =
      `Week of <b>${day(d.target_date, true)}</b> · ${d.horizon} weeks ahead · data up to ${day(d.issue_date)} · ${d.n_wards} wards`;
    const c = d.crews;
    root.querySelector("[data-crew-hint]").innerHTML = c.unmet
      ? `<b>${n(c.needed)}</b> needed · <span class="txt-crit">${n(c.unmet)} short</span> across ${c.wards_short} wards`
      : `<b>${n(c.needed)}</b> needed · all covered${c.spare ? ` · ${n(c.spare)} spare` : ""}`;
    renderStats(d);
    renderAlerts(d);
    renderTable(d);
    const rule = root.querySelector("[data-rule]");
    if (!rule.firstChild) rule.innerHTML = info(d.priority_rule);
    root.querySelector("[data-rates]").innerHTML = `Units per expected case, least-squares fit on ${esc(d.rates_fit.fit_years)} training years: ` +
      d.supplies.map((s) => `${esc(s.label.toLowerCase())} <b class="num">${s.k.toFixed(3)}</b> (R² ${s.r2_test.toFixed(3)} on ${d.rates_fit.test_years.join("–")})`).join(" · ") +
      `. Required = round(k × expected cases); safety uses the upper end of the 80% range.`;
  }

  function renderStats(d) {
    const slot = root.querySelector("[data-stats]");
    if (!slot.querySelector("[data-crit]")) {
      slot.innerHTML = d.supplies.map((s) => `
        <div class="card res-stat" data-s="${s.key}">
          <div class="res-stat__top"><span>${esc(s.label)}</span><small class="num">k ${s.k.toFixed(2)}/case</small></div>
          <b class="fig" data-req>0</b><em>required</em>
          <div class="res-bar" aria-hidden="true"><i data-bar></i></div>
          <p data-line>&nbsp;</p><small data-safety>&nbsp;</small>
        </div>`).join("") + `
        <div class="card res-stat res-stat--crit">
          <div class="res-stat__top"><span>Critical deficits</span>${info("High or Critical risk wards that cannot get 100% of any supply.")}</div>
          <b class="fig" data-crit>0</b><em>wards</em>
          <p data-crit-line>&nbsp;</p><small data-warn>&nbsp;</small>
        </div>`;
    }
    for (const s of d.supplies) {
      const t = d.totals[s.key];
      const el = slot.querySelector(`[data-s="${s.key}"]`);
      el.querySelector("[data-req]").textContent = n(t.required);
      el.querySelector("[data-bar]").style.width = `${t.required ? (100 * t.available) / t.required : 100}%`;
      el.querySelector("[data-line]").innerHTML = t.unmet
        ? `<b>${n(t.available)}</b> available · <span class="txt-crit">${n(t.unmet)} short</span> in ${t.wards_short} ward${t.wards_short === 1 ? "" : "s"}`
        : `<b>${n(t.available)}</b> available · every ward covered`;
      el.querySelector("[data-safety]").textContent = `Safety stock (upper forecast): ${n(t.safety)}`;
    }
    const crit = slot.querySelector("[data-crit]");
    crit.textContent = String(d.n_critical_deficit);
    crit.closest(".res-stat").classList.toggle("is-zero", d.n_critical_deficit === 0);
    slot.querySelector("[data-crit-line]").textContent = d.n_critical_deficit
      ? "High/Critical wards can't be fully supplied"
      : "Every High/Critical ward is fully supplied";
    slot.querySelector("[data-warn]").textContent = `${d.n_warning} lower-risk warning${d.n_warning === 1 ? "" : "s"} · ${d.crews.wards_short} wards short of crews`;
  }

  function renderAlerts(d) {
    const box = root.querySelector("[data-alerts]");
    root.querySelector("[data-alerts-sub]").textContent = d.alerts.length
      ? `${d.n_critical_deficit} critical, ${d.n_warning} warning${d.n_warning === 1 ? "" : "s"}, in priority order`
      : "No ward is short of any supply";
    if (!d.alerts.length) {
      box.innerHTML = `<p class="res-ok">All ${d.n_wards} wards get 100% of saline, kits and beds at ${d.stock_pct}% stock.</p>`;
      return;
    }
    const MAX = 8;
    box.innerHTML = `<ul class="alerts">${d.alerts.slice(0, MAX).map((a) => `
      <li class="alert tier-${a.tier}">
        <span class="flag ${FLAG_CLS[a.level]}">${esc(a.level)}</span>
        <span class="alert__w"><b>${esc(a.label)}</b> <span>${esc(a.city)} · #${a.priority} · ${esc(a.tier)}</span></span>
        <span class="alert__s">${a.short.map((x) => `${esc(x.supply)} <b class="num">${x.allocated}/${x.required}</b>`).join(" · ")}</span>
      </li>`).join("")}</ul>` +
      (d.alerts.length > MAX ? `<p class="res-more">+${d.alerts.length - MAX} more in the plan below</p>` : "");
  }

  function covCell(s, critical) {
    if (!s.required) return `<td class="cov cov--none"><span>–</span></td>`;
    const cls = s.coverage >= 1 ? "cov--full" : critical ? "cov--crit" : "cov--part";
    return `<td class="cov ${cls}" title="Allocated ${s.allocated} of ${s.required} (${pc(s.coverage)}). Safety stock ${s.safety}.">
      <span class="cov__lane"><i style="width:${Math.min(100, s.coverage * 100)}%"></i></span>
      <span class="cov__txt num">${s.allocated}<span>/${s.required}</span></span></td>`;
  }

  function renderTable(d) {
    const showCity = d.scope === "all";
    root.querySelector("[data-table]").innerHTML = `
      <table class="res-table">
        <caption class="sr-only">Allocation plan for ${esc(d.scope_label)}, by priority</caption>
        <thead><tr>
          <th scope="col" class="r">#</th><th scope="col">Ward</th><th scope="col">Risk</th>
          <th scope="col" class="r">Cases</th>
          ${d.supplies.map((s) => `<th scope="col">${esc(s.label)}</th>`).join("")}
          <th scope="col" class="r">Crews</th><th scope="col">Status</th>
        </tr></thead>
        <tbody>${d.wards.map((w) => {
          const hi = w.tier === "High" || w.tier === "Critical";
          return `<tr class="tier-${w.tier}${w.flag ? " is-short" : ""}">
            <td class="r num res-pri">${w.priority}</td>
            <td><button type="button" class="res-ward" data-ward="${esc(w.ward)}" data-city="${esc(w.city)}"
                 aria-label="Open ${esc(w.label)}, ${esc(w.city)}">${esc(w.label)}${showCity ? `<small>${esc(w.city)}</small>` : ""}</button></td>
            <td><span class="tier tier--sm tier-${w.tier}">${w.tier}</span></td>
            <td class="r num" title="Likely ${Math.round(w.low_80)}–${Math.ceil(w.high_80)} · density weight ×${fmt(w.density_weight, 2)}">${fmt(w.expected)}</td>
            ${d.supplies.map((s) => covCell(w.supplies[s.key], hi)).join("")}
            <td class="r num ${w.crews_assigned < w.crews_needed ? "txt-crit" : ""}">${w.crews_needed ? `${w.crews_assigned}/${w.crews_needed}` : "–"}</td>
            <td>${w.flag ? `<span class="flag ${FLAG_CLS[w.flag]}">${esc(w.flag)}</span>` : '<span class="flag flag--ok">Covered</span>'}</td>
          </tr>`;
        }).join("")}</tbody>
      </table>`;
  }

  root.querySelector("[data-table]").addEventListener("click", (e) => {
    const b = e.target.closest("[data-ward]");
    if (b) setState({ view: "ward", city: b.dataset.city, ward: b.dataset.ward, rain: 0, temp: 0, hum: 0 });
  });

  return {
    update(s) {
      scopeBox.set(s.scope); hSeg.set(s.h); stepper.set(s.date); floorSeg.set(s.floor);
      if (document.activeElement !== stock) paintStock(s.stock);
      if (document.activeElement !== crews) crews.value = s.crews;
      load(s);
    },
    destroy() {},
  };
}
