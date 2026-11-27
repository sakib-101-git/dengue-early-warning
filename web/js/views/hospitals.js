// Hospitals: projected bed demand vs (assumed) capacity, and greedy overflow routing within each city.
import { api, isAbort } from "../api.js";
import { getMeta, setState } from "../state.js";
import { combobox, day, debounce, errorBox, esc, field, fmt, info, segmented, weekStepper } from "../ui.js";

const n = (v) => Number(v).toLocaleString("en-US");
const pc = (v) => `${Math.round(v * 100)}%`;
const s_ = (v, one, many = `${one}s`) => `${n(v)} ${v === 1 ? one : many}`;
const CAP = { min: 50, max: 200, step: 5 };
const STATUS = {
  unresolved: ["flag--crit", "Over capacity"],
  relieved: ["flag--warn", "Relieved"],
  receiving: ["flag--info", "Receiving"],
  "near capacity": ["flag--warn", "Near capacity"],
  ok: ["flag--ok", "OK"],
};
const ASSUMPTION = "Assumed capacity. The dataset has no bed-capacity column, so each ward's baseline is " +
  "max(2, ceil(P95 of its weekly beds needed in 2017–2022)): the beds it needed in a bad-but-normal week. " +
  "Drag the scale to test fewer or more beds.";

export function mountHospitals(root, st) {
  const meta = getMeta();
  root.innerHTML = `
    <header class="pagehead"><div>
      <div class="crumbs"><span>Hospital surge</span></div>
      <h1 data-title>&nbsp;</h1>
      <p data-sub>&nbsp;</p>
    </div></header>
    <section class="toolbar toolbar--city" aria-label="Forecast settings"></section>
    <section class="card res-controls" aria-labelledby="hc-t">
      <div class="card__head"><div><h2 id="hc-t">Bed capacity</h2>
        <p>Overflow is sent to wards with free beds in the same city. It is never sent across cities.</p></div>
        <div class="right"><span class="chip">Assumption</span>${info(ASSUMPTION)}</div></div>
      <div class="hos-controls">
        <div class="slider res-slider">
          <div class="slider__top"><label for="hos-cap">Capacity scale <small class="hos-sub">100% = derived capacity</small></label><output for="hos-cap" class="is-on"></output></div>
          <input id="hos-cap" type="range" min="${CAP.min}" max="${CAP.max}" step="${CAP.step}">
          <div class="slider__ends" aria-hidden="true"><span>50%</span><span>200%</span></div>
        </div>
        <div class="field" data-basis></div>
      </div>
    </section>
    <section class="res-stats" aria-label="Bed totals" data-stats>
      ${'<div class="card"><div class="skel skel--line" style="width:40%"></div><div class="skel skel--big" style="width:70%"></div></div>'.repeat(4)}
    </section>
    <section class="card" aria-labelledby="cu-t">
      <div class="card__head"><div><h2 id="cu-t">City bed utilisation</h2><p data-cities-sub>&nbsp;</p></div></div>
      <div data-cities></div>
    </section>
    <div class="hos-split">
      <section class="card" aria-labelledby="wd-t">
        <div class="card__head"><div><h2 id="wd-t">Demand vs capacity by ward</h2>
          <p>Bar = projected beds needed. Tick = capacity. Red = beds over capacity after routing.</p></div></div>
        <div data-wards>${'<div class="skel skel--line" style="height:18px;margin:12px 0"></div>'.repeat(8)}</div>
      </section>
      <section class="card hos-tx" aria-labelledby="tx-t">
        <div class="card__head"><div><h2 id="tx-t">Transfers</h2><p data-tx-sub>&nbsp;</p></div></div>
        <div data-tx></div>
      </section>
    </div>
    <p class="res-foot" data-rules></p>`;

  // ---- toolbar
  const bar = root.querySelector(".toolbar");
  const scopeOpts = [{ value: "all", label: "All cities", hint: "60 wards" },
    ...meta.cities.map((c) => ({ value: c.name, label: c.name, hint: `${c.wards.length} wards` }))];
  const scopeBox = combobox({ label: "City", options: scopeOpts, value: st.scope, onChange: (v) => setState({ scope: v }) });
  const hSeg = segmented({ label: "Weeks ahead", items: meta.horizons.map((h) => ({ value: h, label: `${h} weeks` })), value: st.h,
    onChange: (v) => setState({ h: Number(v) }) });
  const stepper = weekStepper({ value: st.date, min: meta.min_date, max: meta.max_date, onChange: (v) => setState({ date: v }) });
  bar.append(field("City", scopeBox.el), field("Weeks ahead", hSeg.el, { cls: "field--h" }),
             field("Data up to", stepper.el, { cls: "field--date", tip: "Beds are planned for the forecast week: data-up-to week + weeks ahead." }));

  // ---- controls
  const cap = root.querySelector("#hos-cap");
  const capOut = root.querySelector('output[for="hos-cap"]');
  const paintCap = (v) => {
    cap.value = v;
    cap.style.setProperty("--p", `${((v - CAP.min) / (CAP.max - CAP.min)) * 100}%`);
    cap.style.setProperty("--z", `${((100 - CAP.min) / (CAP.max - CAP.min)) * 100}%`);
    capOut.textContent = `${v}%`;
    cap.setAttribute("aria-valuetext", `${v}% of derived capacity`);
  };
  const pushCap = debounce((v) => setState({ cap: v }, { push: false }), 160);
  cap.addEventListener("input", () => { paintCap(Number(cap.value)); pushCap(Number(cap.value)); });

  const basisSeg = segmented({ label: "Plan beds for",
    items: [{ value: "surge", label: "Surge (upper forecast)" }, { value: "expected", label: "Expected" }],
    value: st.basis, onChange: (v) => setState({ basis: v }, { push: false }) });
  root.querySelector("[data-basis]").append(
    Object.assign(document.createElement("div"), { className: "label", innerHTML: `Plan beds for${info("Surge uses the upper end of the 80% forecast range, the safer figure for beds. Expected uses the central forecast. Beds = learned beds-per-case rate × cases.")}` }),
    basisSeg.el);

  // ---- data
  let lastKey = "", loaded = false;
  async function load(s) {
    const key = [s.scope, s.date, s.h, s.cap, s.basis].join("|");
    if (key === lastKey) return;
    lastKey = key;
    if (loaded) root.classList.add("is-busy");
    try {
      const d = await api("overflow", { city: s.scope, date: s.date, h: s.h, capacity_pct: s.cap, basis: s.basis }, "overflow");
      render(d);
      loaded = true;
    } catch (e) {
      if (isAbort(e)) return;
      lastKey = "";
      root.querySelector("[data-stats]").replaceChildren(errorBox(e.message, () => load(s)));
    } finally { root.classList.remove("is-busy"); }
  }

  function render(d) {
    root.querySelector("[data-title]").textContent = `Hospital beds · ${d.scope_label}`;
    root.querySelector("[data-sub]").innerHTML =
      `Week of <b>${day(d.target_date, true)}</b> · ${d.horizon} weeks ahead · data up to ${day(d.issue_date)} · ` +
      `${d.basis === "surge" ? "surge" : "expected"} demand · capacity ${d.capacity_pct}%`;
    renderStats(d);
    renderCities(d);
    renderWards(d);
    renderTransfers(d);
    root.querySelector("[data-rules]").innerHTML =
      `${esc(d.capacity_rule)} ${esc(d.routing_rule)} Beds needed = round(<b class="num">${d.k_beds.toFixed(3)}</b> × cases), ` +
      `the beds-per-case rate learned in Resources.`;
  }

  function stat(label, fig, unit, line, small, cls = "", tip = "") {
    return `<div class="card res-stat ${cls}">
      <div class="res-stat__top"><span>${esc(label)}</span>${tip ? info(tip) : ""}</div>
      <b class="fig">${fig}</b><em>${unit}</em><p>${line}</p><small>${small}</small></div>`;
  }

  function renderStats(d) {
    const t = d.totals;
    const util = `<div class="res-bar hos-ubar${t.utilisation > 1 ? " is-over" : ""}" aria-hidden="true"><i style="width:${Math.min(100, t.utilisation * 100)}%"></i></div>`;
    root.querySelector("[data-stats]").innerHTML =
      stat(d.city === "all" ? "Bed utilisation, all cities" : "City bed utilisation", pc(t.utilisation), "of assumed capacity",
        `<b>${n(t.demand)}</b> beds needed · <b>${n(t.capacity)}</b> available`, "", t.utilisation > 1 ? "res-stat--crit" : "",
        "Projected beds needed ÷ assumed capacity, before any routing. Over 100% means the scope as a whole is short of beds.")
        .replace("<p>", `${util}<p>`) +
      stat("Overflow before routing", n(t.overflow_before), "beds over capacity",
        t.overflow_before ? `in <b>${n(t.wards_over)}</b> ward${t.wards_over === 1 ? "" : "s"}` : "No ward is over capacity",
        `Free beds before routing: ${n(t.headroom_before)}`) +
      stat("Routed within city", n(t.transferred), "patients moved",
        `${s_(d.transfers.length, "transfer")}`, "Same city only, to non-Critical wards") +
      stat("Unresolved overflow", n(t.unresolved), "beds still short",
        t.unresolved ? `<span class="txt-crit">${n(t.wards_unresolved)} ward${t.wards_unresolved === 1 ? "" : "s"} still over capacity</span>` : "Every patient has a bed",
        `Remaining free beds: ${n(t.remaining_headroom)}${t.headroom_reserved ? ` (${n(t.headroom_reserved)} held by Critical wards)` : ""}`,
        `res-stat--crit${t.unresolved ? "" : " is-zero"}`,
        "Beds still needed after every free bed in the same city has been used. Escalate these to the city or a regional hospital.");
  }

  function renderCities(d) {
    root.querySelector("[data-cities-sub]").textContent = d.city === "all"
      ? "Projected demand as a share of assumed capacity. Select a city to see its wards."
      : "Projected demand as a share of assumed capacity";
    const max = Math.max(1.2, ...d.cities.map((c) => c.utilisation));
    root.querySelector("[data-cities]").innerHTML = `<ul class="hos-cities">${d.cities.map((c) => {
      const over = c.utilisation > 1;
      return `<li>
        <button type="button" class="hos-city" data-city="${esc(c.city)}" ${d.city !== "all" ? "disabled" : ""}
          aria-label="${esc(c.city)}: ${pc(c.utilisation)} utilisation${d.city === "all" ? ", show its wards" : ""}">${esc(c.city)}<small>${c.n_wards} wards</small></button>
        <span class="hos-lane" role="img" aria-label="${n(c.demand)} of ${n(c.capacity)} beds">
          <i class="${over ? "is-over" : c.utilisation >= 0.85 ? "is-near" : ""}" style="width:${(Math.min(c.utilisation, max) / max) * 100}%"></i>
          <b style="left:${(1 / max) * 100}%" title="100% of capacity"></b>
        </span>
        <span class="hos-city__n num"><b>${pc(c.utilisation)}</b> ${n(c.demand)}/${n(c.capacity)}</span>
        <span class="hos-city__x">${c.unresolved
          ? `<span class="flag flag--crit">${n(c.unresolved)} unresolved</span>`
          : c.transferred ? `<span class="flag flag--warn">${n(c.transferred)} routed</span>` : '<span class="flag flag--ok">No overflow</span>'}
          <small>${n(c.remaining_headroom)} free after</small></span>
      </li>`;
    }).join("")}</ul>`;
  }

  function renderWards(d) {
    const max = Math.max(...d.wards.map((w) => Math.max(w.demand, w.capacity)), 1);
    const byCity = d.cities.map((c) => ({ c, ws: d.wards.filter((w) => w.city === c.city) }));
    root.querySelector("[data-wards]").innerHTML = byCity.map(({ c, ws }) => `
      ${d.city === "all" ? `<h3 class="hos-group">${esc(c.city)} <span>${pc(c.utilisation)} · ${n(c.demand)}/${n(c.capacity)} beds</span></h3>` : ""}
      <ul class="hos-wards">${ws.map((w) => {
        const [cls, label] = STATUS[w.status];
        const kept = w.demand - w.sent;                       // beds used by own patients after routing
        const within = Math.min(kept, w.capacity);
        const moves = w.sent ? `→ ${n(w.sent)} out` : w.received ? `← ${n(w.received)} in` : "";
        const tip = `${w.label}, ${w.city} (${w.tier}): ${w.demand} beds needed (expected ${w.demand_expected}, surge ${w.demand_surge}), ` +
          `capacity ${w.capacity} (baseline ${w.baseline_capacity}). ${w.sent ? `Sends ${w.sent}. ` : ""}${w.received ? `Receives ${w.received}. ` : ""}` +
          `After routing: ${w.load_after}/${w.capacity}${w.net_deficit ? `, ${w.net_deficit} still short` : ""}.`;
        return `<li class="hos-ward tier-${w.tier}" title="${esc(tip)}">
          <button type="button" class="res-ward" data-ward="${esc(w.ward)}" data-wcity="${esc(w.city)}" aria-label="Open ${esc(w.label)}, ${esc(w.city)}">${esc(w.label)}</button>
          <span class="tier tier--sm tier-${w.tier}">${w.tier}</span>
          <span class="hos-lane hos-lane--w" role="img" aria-label="${esc(tip)}">
            <i style="width:${(within / max) * 100}%"></i>${w.received ? `<i class="is-in" style="width:${(w.received / max) * 100}%"></i>` : ""}${w.net_deficit ? `<i class="is-over" style="width:${(w.net_deficit / max) * 100}%"></i>` : ""}${w.sent ? `<i class="is-out" style="width:${(w.sent / max) * 100}%"></i>` : ""}
            <b style="left:${(w.capacity / max) * 100}%"></b>
          </span>
          <span class="hos-ward__n num">${w.demand}<span>/${w.capacity}</span></span>
          <span class="hos-ward__m num">${moves}</span>
          <span class="flag ${cls}">${label}</span>
        </li>`;
      }).join("")}</ul>`).join("") +
      `<p class="hos-legend"><span><i></i>own patients</span><span><i class="is-out"></i>sent out</span><span><i class="is-in"></i>received</span><span><i class="is-over"></i>still over capacity</span><span><b></b>capacity</span></p>`;
  }

  function renderTransfers(d) {
    const t = d.transfers;
    root.querySelector("[data-tx-sub]").textContent = t.length
      ? `${s_(t.length, "transfer")}, ${s_(d.totals.transferred, "patient")}, largest deficit first`
      : "No transfers needed";
    const box = root.querySelector("[data-tx]");
    if (!t.length) {
      box.innerHTML = `<p class="res-ok">${d.totals.overflow_before
        ? "There are no free beds in a non-Critical ward of the same city, so overflow can't be routed. Raise capacity or escalate."
        : `Every ward has enough beds at ${d.capacity_pct}% capacity. Try surge demand or a lower capacity scale.`}</p>`;
      return;
    }
    box.innerHTML = `<ol class="hos-txlist">${t.map((x, i) => `
      <li><span class="hos-tx__i num">${i + 1}</span>
        <span class="hos-tx__r"><b>${esc(x.from_label)}</b><span aria-label="to"> → </span><b>${esc(x.to_label)}</b>
        ${d.city === "all" ? `<small>${esc(x.city)}</small>` : ""}</span>
        <span class="hos-tx__p num">${s_(x.patients, "patient")}</span></li>`).join("")}</ol>`;
  }

  root.addEventListener("click", (e) => {
    const c = e.target.closest("[data-city]");
    if (c && !c.disabled) { setState({ scope: c.dataset.city }); return; }
    const b = e.target.closest("[data-ward]");
    if (b) setState({ view: "ward", city: b.dataset.wcity, ward: b.dataset.ward, rain: 0, temp: 0, hum: 0 });
  });

  return {
    update(s) {
      scopeBox.set(s.scope); hSeg.set(s.h); stepper.set(s.date); basisSeg.set(s.basis);
      if (document.activeElement !== cap) paintCap(s.cap);
      load(s);
    },
    destroy() {},
  };
}
