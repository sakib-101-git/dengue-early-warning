// Simulator: what-if vector control. Crews cut a ward's water-logging index; the LightGBM models re-run for 2-4 weeks ahead.
import { api, isAbort } from "../api.js";
import { makeChart, simBarsOption } from "../charts.js";
import { MAX_CREWS_PER_WARD, decodeDeploy, encodeDeploy, getMeta, setState } from "../state.js";
import { combobox, day, debounce, errorBox, esc, field, fmt, info, weekStepper } from "../ui.js";

const n = (v) => Number(v).toLocaleString("en-US");
const pc = (v) => `${Math.round(v * 100)}%`;
const EFFECT = { min: 5, max: 20, step: 1 };
const TIER_RANK = { Low: 0, Medium: 1, High: 2, Critical: 3 };

export function mountSimulator(root, st) {
  const meta = getMeta();
  root.innerHTML = `
    <header class="pagehead"><div>
      <div class="crumbs"><span>Vector control simulator</span></div>
      <h1 data-title>&nbsp;</h1>
      <p data-sub>&nbsp;</p>
    </div></header>
    <section class="toolbar toolbar--city" aria-label="Forecast settings"></section>
    <section class="card res-controls" aria-labelledby="sim-t">
      <div class="card__head"><div><h2 id="sim-t">Spraying teams</h2>
        <p>Add crews to wards in the ranking below, or let the planner place them where they prevent the most cases.</p></div>
        <div class="right"><span class="chip">Model counterfactual</span><span data-method></span></div></div>
      <div class="res-controls__grid sim-controls">
        <div class="slider res-slider">
          <div class="slider__top"><label for="sim-effect">Effect per crew <small class="hos-sub">cut in water-logging index</small></label><output for="sim-effect" class="is-on"></output></div>
          <input id="sim-effect" type="range" min="${EFFECT.min}" max="${EFFECT.max}" step="${EFFECT.step}">
          <div class="slider__ends" aria-hidden="true"><span>5%</span><span>20%</span></div>
          <small class="res-hint" data-effect-hint>&nbsp;</small>
        </div>
        <div class="field res-crews">
          <label for="sim-auto">Auto-deploy crews</label>
          <div class="numstep">
            <button type="button" class="btn btn--sm" data-astep="-1" aria-label="One crew fewer">−</button>
            <input id="sim-auto" type="number" inputmode="numeric" min="0" max="1000" step="1" value="10">
            <button type="button" class="btn btn--sm" data-astep="1" aria-label="One crew more">+</button>
            <button type="button" class="btn btn--primary sim-go" data-auto>Auto-deploy</button>
          </div>
          <small class="res-hint" data-auto-hint>Greedy: each crew goes where it prevents the most extra cases.</small>
        </div>
        <div class="field sim-deployed">
          <div class="label">Deployed now</div>
          <div class="sim-deployed__row"><b class="num" data-deployed>0</b><span data-deployed-sub>crews</span>
            <button type="button" class="btn btn--sm" data-clear disabled>Clear all</button></div>
          <small class="res-hint" data-deployed-hint>&nbsp;</small>
        </div>
      </div>
    </section>
    <section class="res-stats" aria-label="Impact" data-stats>
      ${'<div class="card"><div class="skel skel--line" style="width:40%"></div><div class="skel skel--big" style="width:70%"></div></div>'.repeat(4)}
    </section>
    <div class="sim-split">
      <section class="card" aria-labelledby="ch-t">
        <div class="card__head"><div><h2 id="ch-t">Forecast cases in deployed wards</h2>
          <p data-chart-sub>Without crews vs with crews, 2, 3 and 4 weeks ahead</p></div></div>
        <div class="chart sim-chart" data-chart role="img" aria-label="Forecast cases without and with crews"></div>
        <p class="res-ok sim-empty" data-empty hidden>No crews deployed yet. Use + in the ranking, or auto-deploy.</p>
      </section>
      <section class="card" aria-labelledby="im-t">
        <div class="card__head"><div><h2 id="im-t">Impact by ward</h2><p data-impact-sub>&nbsp;</p></div></div>
        <div data-impact></div>
      </section>
    </div>
    <section class="card" aria-labelledby="rk-t">
      <div class="card__head"><div><h2 id="rk-t">Intervention ROI ranking</h2>
        <p>Wards ranked by cases one crew prevents over the next 2–4 weeks. Use − and + to place crews.</p></div>
        <div class="right">${info("1-crew ROI = forecast cases prevented over 2, 3 and 4 weeks ahead by one crew. Best ROI = most cases per crew at any crew count (tree models can need 2 crews before the forecast moves). Max = cases prevented at the 50% cap.")}</div></div>
      <div class="res-table-wrap" data-rank>${'<div class="skel skel--line" style="height:22px;margin:14px 0"></div>'.repeat(8)}</div>
      <button type="button" class="btn btn--sm sim-more" data-more hidden></button>
      <p class="res-foot" data-foot></p>
    </section>`;

  // ---- toolbar
  const bar = root.querySelector(".toolbar");
  const scopeOpts = [{ value: "all", label: "All cities", hint: "60 wards" },
    ...meta.cities.map((c) => ({ value: c.name, label: c.name, hint: `${c.wards.length} wards` }))];
  // crews outside the new scope are dropped by state.sanitize()
  const scopeBox = combobox({ label: "Scope", options: scopeOpts, value: st.scope, onChange: (v) => setState({ scope: v }) });
  const stepper = weekStepper({ value: st.date, min: meta.min_date, max: meta.max_date, onChange: (v) => setState({ date: v }) });
  bar.append(field("Scope", scopeBox.el),
             field("Data up to", stepper.el, { cls: "field--date", tip: "Crews are sent out after this week. Their effect is forecast 2, 3 and 4 weeks later." }));

  // ---- effect slider
  const eff = root.querySelector("#sim-effect");
  const effOut = root.querySelector('output[for="sim-effect"]');
  const paintEff = (v) => {
    eff.value = v;
    eff.style.setProperty("--p", `${((v - EFFECT.min) / (EFFECT.max - EFFECT.min)) * 100}%`);
    eff.style.setProperty("--z", "0%");
    effOut.textContent = `${v}%`;
    eff.setAttribute("aria-valuetext", `${v}% per crew`);
    const r = (c) => Math.min(0.5, 1 - (1 - v / 100) ** c);
    const cap = Math.ceil(Math.log(0.5) / Math.log(1 - v / 100) - 1e-9);
    root.querySelector("[data-effect-hint]").innerHTML =
      `1 crew <b>−${pc(r(1))}</b> · 2 <b>−${pc(r(2))}</b> · 3 <b>−${pc(r(3))}</b> · cap <b>−50%</b> at ${cap} crews`;
  };
  const pushEff = debounce((v) => setState({ effect: v }, { push: false }), 160);
  eff.addEventListener("input", () => { paintEff(Number(eff.value)); pushEff(Number(eff.value)); });

  // ---- auto-deploy
  const autoIn = root.querySelector("#sim-auto");
  const clampN = (v) => Math.min(1000, Math.max(0, Math.round(Number(v) || 0)));
  root.querySelector(".numstep").addEventListener("click", (e) => {
    const b = e.target.closest("[data-astep]"); if (!b) return;
    autoIn.value = clampN(Number(autoIn.value) + Number(b.dataset.astep) * (e.shiftKey ? 5 : 1));
  });
  autoIn.addEventListener("change", () => { autoIn.value = clampN(autoIn.value); });
  autoIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); autoDeploy(); } });
  const autoBtn = root.querySelector("[data-auto]");
  const autoHint = root.querySelector("[data-auto-hint]");
  let autoNote = "";
  async function autoDeploy() {
    const N = clampN(autoIn.value);
    autoIn.value = N;
    autoBtn.disabled = true;
    try {
      const d = await api("simulate", { scope: cur.scope, date: cur.date, effect: cur.effect, auto: N }, "sim-auto");
      autoNote = N && d.crews_deployed < N
        ? `Placed ${n(d.crews_deployed)} of ${n(N)}: more crews would not prevent more cases.`
        : `Placed ${n(d.crews_deployed)} crew${d.crews_deployed === 1 ? "" : "s"} in ${d.wards_deployed} ward${d.wards_deployed === 1 ? "" : "s"} by marginal ROI.`;
      autoHint.textContent = autoNote;
      plan = { ...d.crews };
      setState({ deploy: encodeDeploy(plan) });
    } catch (e) {
      if (!isAbort(e)) autoHint.innerHTML = `<span class="txt-crit">${esc(e.message)}</span>`;
    } finally { autoBtn.disabled = false; }
  }
  autoBtn.addEventListener("click", autoDeploy);
  root.querySelector("[data-clear]").addEventListener("click", () => { plan = {}; paintCrews(); setState({ deploy: "" }); });

  // ---- per-ward steppers (in the ranking table)
  let plan = decodeDeploy(st.deploy);
  const pushPlan = debounce(() => setState({ deploy: encodeDeploy(plan) }, { push: false }), 220);
  function paintCrews() {
    root.querySelectorAll("[data-crew-of]").forEach((el) => {
      const c = plan[el.dataset.crewOf] || 0;
      el.textContent = c;
      el.closest("tr").classList.toggle("is-deployed", c > 0);
      el.parentElement.querySelector('[data-d="-1"]').disabled = c <= 0;
      el.parentElement.querySelector('[data-d="1"]').disabled = c >= MAX_CREWS_PER_WARD;
    });
    const total = Object.values(plan).reduce((a, b) => a + b, 0);
    root.querySelector("[data-deployed]").textContent = n(total);
    root.querySelector("[data-deployed-sub]").textContent = `crew${total === 1 ? "" : "s"} in ${Object.keys(plan).length} ward${Object.keys(plan).length === 1 ? "" : "s"}`;
    root.querySelector("[data-clear]").disabled = total === 0;
  }
  root.querySelector("[data-rank]").addEventListener("click", (e) => {
    const b = e.target.closest("[data-step]");
    if (b) {
      const w = b.dataset.step;
      const c = Math.min(MAX_CREWS_PER_WARD, Math.max(0, (plan[w] || 0) + Number(b.dataset.d)));
      if (c) plan[w] = c; else delete plan[w];
      autoNote = ""; autoHint.textContent = "Greedy: each crew goes where it prevents the most extra cases.";
      paintCrews();
      pushPlan();
      return;
    }
    const a = e.target.closest("[data-ward]");
    if (a) setState({ view: "ward", city: a.dataset.city, ward: a.dataset.ward, rain: 0, temp: 0, hum: 0 });
  });
  root.querySelector("[data-impact]").addEventListener("click", (e) => {
    const a = e.target.closest("[data-ward]");
    if (a) setState({ view: "ward", city: a.dataset.city, ward: a.dataset.ward, rain: 0, temp: 0, hum: 0 });
  });

  // ---- ranking: top wards (+ any ward with crews) unless expanded
  const TOP = 15;
  let showAll = false;
  root.querySelector("[data-more]").addEventListener("click", () => {
    showAll = !showAll;
    if (lastRank && lastSim) renderRank(lastRank, lastSim);
    paintCrews();
  });

  // ---- data
  const chart = makeChart(root.querySelector("[data-chart]"));
  let cur = st, rankKey = "", simKey = "", loaded = false, lastRank = null, lastSim = null;
  async function load(s) {
    cur = s;
    const rk = [s.scope, s.date, s.effect].join("|");
    const sk = [s.scope, s.date, s.effect, s.deploy].join("|");
    if (rk === rankKey && sk === simKey) return;
    if (loaded) root.classList.add("is-busy");
    try {
      const [r, d] = await Promise.all([
        rk === rankKey ? Promise.resolve(lastRank) : api("simulate/rank", { scope: s.scope, date: s.date, effect: s.effect }, "sim-rank"),
        api("simulate", { scope: s.scope, date: s.date, effect: s.effect, crews: s.deploy }, "sim"),
      ]);
      rankKey = rk; simKey = sk; lastRank = r; lastSim = d;
      render(r, d);
      loaded = true;
    } catch (e) {
      if (isAbort(e)) return;
      rankKey = simKey = "";
      root.querySelector("[data-stats]").replaceChildren(errorBox(e.message, () => load(cur)));
    } finally { root.classList.remove("is-busy"); }
  }

  function render(r, d) {
    const hs = d.horizons;
    root.querySelector("[data-title]").textContent = `Vector control · ${d.scope_label}`;
    root.querySelector("[data-sub]").innerHTML =
      `Crews sent after data up to <b>${day(d.issue_date, true)}</b> · impact on the weeks of ${hs.map((x) => day(x.target_date)).join(", ")} ` +
      `(${hs.map((x) => x.h).join(", ")} weeks ahead) · ${d.effect_pct}% per crew`;
    const m = root.querySelector("[data-method]");
    if (!m.firstChild) m.innerHTML = info(d.method);
    renderStats(d);
    renderChart(d);
    renderImpact(d);
    renderRank(r, d);
    paintCrews();
    root.querySelector("[data-deployed-hint]").textContent = d.wards_deployed
      ? `${pc(d.totals.share_of_scope)} of the ${n(Math.round(d.totals.scope_baseline_cases))} cases forecast in scope over 2–4 weeks`
      : `${n(Math.round(d.totals.scope_baseline_cases))} cases forecast in scope over 2–4 weeks`;
    root.querySelector("[data-foot]").innerHTML = `${esc(d.method)} Beds and saline = round(k × cases) with the learned rates: ` +
      `beds <b class="num">${d.rates.beds.toFixed(3)}</b>, saline <b class="num">${d.rates.saline.toFixed(3)}</b> per case. ` +
      `Bed-weeks add up the beds needed in each of the three forecast weeks.`;
  }

  function stat(label, fig, unit, line, small, tip = "", cls = "") {
    return `<div class="card res-stat ${cls}">
      <div class="res-stat__top"><span>${esc(label)}</span>${tip ? info(tip) : ""}</div>
      <b class="fig">${fig}</b><em>${unit}</em><p>${line}</p><small>${small}</small></div>`;
  }

  function renderStats(d) {
    const t = d.totals, p = t.prevented;
    const by = t.by_horizon;
    const sum = (k) => by.reduce((a, r) => a + r[k], 0);
    root.querySelector("[data-stats]").innerHTML =
      stat("Cases prevented", fmt(p.cases), "over 2–4 weeks ahead",
        d.wards_deployed ? `<b>${fmt(sum("baseline_cases"))}</b> → <b>${fmt(sum("scenario_cases"))}</b> in deployed wards` : "Deploy crews to see the effect",
        by.map((r) => `${r.h}w −${fmt(r.prevented_cases)}`).join(" · "),
        "Forecast cases without crews minus forecast cases with crews, added over the 2, 3 and 4 week forecasts.", "sim-stat--good") +
      stat("Bed-weeks freed", n(p.beds), "hospital bed-weeks",
        `<b>${n(sum("baseline_beds"))}</b> → <b>${n(sum("scenario_beds"))}</b> bed-weeks`,
        by.map((r) => `${r.h}w ${r.baseline_beds}→${r.scenario_beds}`).join(" · "),
        "Beds needed = round(beds-per-case rate × forecast cases), per forecast week, added over the three weeks.") +
      stat("Saline bags saved", n(p.saline), "bags",
        `<b>${n(sum("baseline_saline"))}</b> → <b>${n(sum("scenario_saline"))}</b> bags`,
        by.map((r) => `${r.h}w ${r.baseline_saline}→${r.scenario_saline}`).join(" · ")) +
      stat("Intervention ROI", fmt(t.roi, 2), "cases prevented per crew",
        `<b>${n(d.crews_deployed)}</b> crew${d.crews_deployed === 1 ? "" : "s"} in <b>${d.wards_deployed}</b> ward${d.wards_deployed === 1 ? "" : "s"}`,
        d.mode === "auto" ? `auto-deployed ${d.auto_requested}` : "manual plan",
        "Total cases prevented over 2–4 weeks ahead ÷ crews deployed.");
  }

  function renderChart(d) {
    const empty = !d.wards_deployed;
    root.querySelector("[data-empty]").hidden = !empty;
    root.querySelector("[data-chart]").hidden = empty;
    root.querySelector("[data-chart-sub]").textContent = empty
      ? "Without crews vs with crews, 2, 3 and 4 weeks ahead"
      : `${d.wards_deployed} deployed ward${d.wards_deployed === 1 ? "" : "s"}, without vs with crews`;
    if (!empty) chart.render(simBarsOption(d.totals.by_horizon), { replace: true });
  }

  function renderImpact(d) {
    const box = root.querySelector("[data-impact]");
    root.querySelector("[data-impact-sub]").textContent = d.wards_deployed
      ? "Most cases prevented first. Arrows show the forecast without → with crews."
      : "Deployed wards will appear here";
    if (!d.wards_deployed) { box.innerHTML = `<p class="res-ok">Tip: auto-deploy ${n(clampN(autoIn.value))} crews to start.</p>`; return; }
    box.innerHTML = `<ul class="sim-impact">${d.wards.map((w) => {
      const drop = TIER_RANK[w.tier_after] < TIER_RANK[w.tier];
      return `<li>
        <div class="sim-impact__w">
          <button type="button" class="res-ward" data-ward="${esc(w.ward)}" data-city="${esc(w.city)}" aria-label="Open ${esc(w.label)}, ${esc(w.city)}">${esc(w.label)}<small>${esc(w.city)}</small></button>
          <span class="tier tier--sm tier-${w.tier}">${w.tier}</span>${drop ? `<span class="sim-arrow" aria-label="drops to">→</span><span class="tier tier--sm tier-${w.tier_after}">${w.tier_after}</span>` : ""}
        </div>
        <div class="sim-impact__h num">${w.horizons.map((x) => `<span title="${x.h} weeks ahead, week of ${day(x.target_date, true)}: beds ${x.baseline.beds}→${x.scenario.beds}, saline ${x.baseline.saline}→${x.scenario.saline}"><i>${x.h}w</i>${fmt(x.baseline.cases)}→<b>${fmt(x.scenario.cases)}</b></span>`).join("")}</div>
        <div class="sim-impact__n num"><span><b>−${fmt(w.prevented.cases)}</b> cases</span><small>${w.crews} crew${w.crews === 1 ? "" : "s"} · index −${pc(w.reduction)} · ROI ${fmt(w.roi, 2)}</small>
          <small>beds −${w.prevented.beds} · saline −${w.prevented.saline}</small></div>
      </li>`;
    }).join("")}</ul>`;
  }

  function renderRank(r, d) {
    const more = root.querySelector("[data-more]");
    const hidden = r.wards.filter((w) => w.rank > TOP && !plan[w.ward]).length;
    more.hidden = !hidden;
    more.textContent = showAll ? `Show top ${TOP} only` : `Show all ${r.wards.length} wards (${hidden} more)`;
    const showCity = r.scope === "all";
    const max = Math.max(0.01, ...r.wards.map((w) => w.roi_1));
    const got = Object.fromEntries(d.wards.map((w) => [w.ward, w]));
    root.querySelector("[data-rank]").innerHTML = `
      <table class="res-table sim-table">
        <caption class="sr-only">Wards in ${esc(r.scope_label)} ranked by cases prevented per crew</caption>
        <thead><tr>
          <th scope="col" class="r">#</th><th scope="col">Ward</th><th scope="col">Peak risk</th>
          <th scope="col" class="r">Cases 2–4 wk</th><th scope="col">1-crew ROI</th>
          <th scope="col" class="r">Best ROI</th><th scope="col" class="r">Max at cap</th>
          <th scope="col">Crews</th><th scope="col" class="r">Prevented</th>
        </tr></thead>
        <tbody>${r.wards.map((w) => {
          const g = got[w.ward];
          const hide = !showAll && w.rank > TOP && !plan[w.ward];
          return `<tr class="tier-${w.tier}"${hide ? " hidden" : ""}>
            <td class="r num res-pri">${w.rank}</td>
            <td><button type="button" class="res-ward" data-ward="${esc(w.ward)}" data-city="${esc(w.city)}"
                 aria-label="Open ${esc(w.label)}, ${esc(w.city)}">${esc(w.label)}${showCity ? `<small>${esc(w.city)}</small>` : ""}</button></td>
            <td><span class="tier tier--sm tier-${w.tier}">${w.tier}</span></td>
            <td class="r num" title="${Object.entries(w.baseline_by_h).map(([h, v]) => `${h} weeks: ${fmt(v)}`).join(" · ")}">${fmt(w.baseline_cases)}</td>
            <td class="cov sim-roi" title="One crew prevents ${fmt(w.roi_1, 2)} cases over 2–4 weeks">
              <span class="cov__lane"><i style="width:${(w.roi_1 / max) * 100}%"></i></span><span class="cov__txt num">${fmt(w.roi_1, 2)}</span></td>
            <td class="r num" title="${fmt(w.best_roi, 2)} cases per crew with ${w.best_roi_crews} crews">${fmt(w.best_roi, 2)}<small class="sim-muted"> @${w.best_roi_crews}</small></td>
            <td class="r num" title="${fmt(w.prevented_max)} cases prevented with ${w.crews_max} crews (50% cap)">${fmt(w.prevented_max)}</td>
            <td><div class="sim-step">
              <button type="button" class="btn btn--sm" data-step="${esc(w.ward)}" data-d="-1" aria-label="One crew fewer for ${esc(w.label)}">−</button>
              <b class="num" data-crew-of="${esc(w.ward)}" aria-live="polite">0</b>
              <button type="button" class="btn btn--sm" data-step="${esc(w.ward)}" data-d="1" aria-label="One crew more for ${esc(w.label)}">+</button>
            </div></td>
            <td class="r num">${g ? `<b>−${fmt(g.prevented.cases)}</b>` : '<span class="sim-muted">–</span>'}</td>
          </tr>`;
        }).join("")}</tbody>
      </table>`;
  }

  return {
    update(s) {
      scopeBox.set(s.scope); stepper.set(s.date);
      if (document.activeElement !== eff) paintEff(s.effect);
      if (s.deploy !== encodeDeploy(plan)) { plan = decodeDeploy(s.deploy); paintCrews(); }
      load(s);
    },
    destroy() {},
  };
}
