import { api, isAbort } from "../api.js";
import { backtestOption, makeChart, modelBarsOption } from "../charts.js";
import { getMeta, setState } from "../state.js";
import { cardHead, countUp, errorBox, esc, field, icon, segmented } from "../ui.js";

const STEPS = [
  ["Weather in", "Weekly rain, temperature and humidity for every ward, plus how flood-prone and crowded it is."],
  ["Look back", "Rain from 0–8 weeks ago and rolling totals, because rain takes 2–4 weeks to become cases."],
  ["Predict", "A LightGBM model, one per horizon, estimates next cases with an 80% likely range."],
  ["Rank", "Expected cases become Low, Medium, High or Critical using cut-offs learned from training years."],
];

export function mountModel(root, st) {
  const meta = getMeta();
  root.innerHTML = `
    <header class="pagehead"><div>
      <div class="crumbs"><span>Model</span></div>
      <h1>How good is the forecast?</h1>
      <p data-sub>Scored on 2024–2025, weeks the model never saw while it was trained or tuned.</p>
    </div></header>
    <section class="toolbar toolbar--model" aria-label="Model settings"></section>
    <section class="stats" data-stats aria-label="Headline accuracy">${'<div class="card stat"><div class="skel skel--big" style="width:60%;margin:0 0 16px"></div><div class="skel skel--line"></div><div class="skel skel--line" style="width:70%"></div></div>'.repeat(4)}</section>
    <section class="card span-6" aria-labelledby="mb-t">
      ${cardHead("Average error by model", "2023 season, cases off per ward-week. Lower is better.", "MAE (mean absolute error) on the 2023 validation season, which picked the model.").replace("<h2>", '<h2 id="mb-t">')}
      <div class="chart chart--bars" data-bars role="img"></div>
    </section>
    <section class="card span-6" aria-labelledby="bt-t">
      ${cardHead("Average error each year", "", "Expanding-window backtest: train on every year before Y, test on Y.").replace("<h2>", '<h2 id="bt-t">')}
      <div class="chart chart--lines" data-lines role="img"></div>
    </section>
    <section class="card" aria-labelledby="st-t">
      ${cardHead("How a forecast is made").replace("<h2>", '<h2 id="st-t">')}
      <div class="steps">${STEPS.map(([a, b]) => `<div class="step"><b>${a}</b>${b}</div>`).join("")}</div>
    </section>
    <details class="more"><summary>All numbers <span>tables behind the charts</span>${icon.chev}</summary><div class="more__body" data-more></div></details>`;

  const hSeg = segmented({ label: "Weeks ahead", items: meta.horizons.map((h) => ({ value: h, label: `${h} weeks` })), value: st.h,
    onChange: (v) => setState({ h: Number(v) }) });
  root.querySelector(".toolbar").append(field("Weeks ahead", hSeg.el, { cls: "field--h" }));

  const bars = makeChart(root.querySelector("[data-bars]"));
  const lines = makeChart(root.querySelector("[data-lines]"));
  let lastH = 0;

  async function load(s) {
    if (s.h === lastH) return;
    lastH = s.h;
    try {
      const d = await api("model", { h: s.h }, "model");
      render(d);
    } catch (e) {
      if (isAbort(e)) return;
      lastH = 0;
      root.querySelector("[data-stats]").replaceChildren(errorBox(e.message, () => load(s)));
    }
  }

  function render(d) {
    const statsEl = root.querySelector("[data-stats]");
    if (!statsEl.querySelector("[data-s0]")) {
      statsEl.innerHTML = [0, 1, 2, 3].map((i) =>
        `<div class="card stat${i === 3 ? " is-accent" : ""}"><b class="fig" data-s${i}>0</b><span data-l${i}></span><small data-m${i}></small></div>`).join("");
    }
    const t = d.test;
    const st = [
      [t.MAE, { decimals: 2 }, "cases off per ward-week, on average", `MAE · RMSE ${t.RMSE.toFixed(2)}`],
      [t.high_risk_recall * 100, { decimals: 0, suffix: "%" }, "of high-risk weeks flagged in advance", "High + Critical recall"],
      [d.coverage_80 * 100, { decimals: 0, suffix: "%" }, "of weeks landed inside the likely range", "80% interval coverage"],
      [d.gain_vs_same_week, { decimals: 1, suffix: "×" }, "more accurate than “same week last year”", `that baseline: ${d.baseline_same_week_mae.toFixed(2)} cases off`],
    ];
    st.forEach(([v, o, l, m], i) => {
      countUp(statsEl.querySelector(`[data-s${i}]`), v, { duration: 1000, ...o });
      statsEl.querySelector(`[data-l${i}]`).textContent = l;
      statsEl.querySelector(`[data-m${i}]`).textContent = m;
    });

    const ours = d.comparison.find((c) => c.ours);
    root.querySelector("[data-bars]").setAttribute("aria-label",
      `Average error per model in 2023. ${d.comparison.map((c) => `${c.label} ${c.MAE.toFixed(2)}`).join(", ")}. Ours: ${ours?.MAE.toFixed(2)}.`);
    bars.render(modelBarsOption(d.comparison), { replace: true });

    root.querySelector("#bt-t").nextElementSibling?.remove();
    root.querySelector("#bt-t").insertAdjacentHTML("afterend", `<p>${d.horizon} weeks ahead highlighted. Lower is better.</p>`);
    const bt = d.backtest[String(d.horizon)];
    root.querySelector("[data-lines]").setAttribute("aria-label",
      `Average error by year, ${d.horizon} weeks ahead: ${bt.map((r) => `${r.year} ${r.MAE.toFixed(2)}`).join(", ")}.`);
    lines.render(backtestOption(d.backtest, d.horizon));

    const th = d.thresholds;
    const row = (cells, cls = "") => `<tr class="${cls}">${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`;
    root.querySelector("[data-more]").innerHTML = `
      <div><h3>Data split</h3><p>Train ${esc(d.split.train)} · validation ${esc(d.split.validation)} · test ${esc(d.split.test)}. ${esc(d.split.rule)}.</p></div>
      <div><h3>Risk cut-offs (cases per ward-week)</h3><p class="num">Low &lt; ${th[0]} ≤ Medium &lt; ${th[1]} ≤ High &lt; ${th[2]} ≤ Critical</p></div>
      <div><h3>Model comparison, 2023 validation (${d.horizon} weeks ahead)</h3><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Model</th><th>MAE</th><th>RMSE</th><th>Tier macro-F1</th><th>High-risk recall</th></tr></thead>
        <tbody>${[...d.comparison].sort((a, b) => a.MAE - b.MAE).map((c) => row([esc(c.label), c.MAE.toFixed(3), c.RMSE.toFixed(3), c.tier_macro_F1.toFixed(3), `${(c.high_risk_recall * 100).toFixed(1)}%`], c.ours ? "is-ours" : "")).join("")}</tbody></table></div></div>
      <div><h3>Held-out test 2024–2025</h3><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Model</th><th>MAE</th><th>RMSE</th><th>Tier macro-F1</th><th>High-risk recall</th></tr></thead>
        <tbody>${row(["Deployed (weather + ward + season)", t.MAE.toFixed(3), t.RMSE.toFixed(3), t.tier_macro_F1.toFixed(3), `${(t.high_risk_recall * 100).toFixed(1)}%`], "is-ours")}
        ${d.test_with_case_history ? row(["With past case counts (ablation)", d.test_with_case_history.MAE.toFixed(3), d.test_with_case_history.RMSE.toFixed(3), d.test_with_case_history.tier_macro_F1.toFixed(3), `${(d.test_with_case_history.high_risk_recall * 100).toFixed(1)}%`]) : ""}</tbody></table></div></div>
      <div><h3>Backtest by year (${d.horizon} weeks ahead)</h3><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Year</th><th>MAE</th><th>RMSE</th><th>Tier macro-F1</th><th>High-risk recall</th></tr></thead>
        <tbody>${bt.map((r) => row([r.year, r.MAE.toFixed(3), r.RMSE.toFixed(3), r.tier_macro_F1.toFixed(3), `${(r.high_risk_recall * 100).toFixed(1)}%`])).join("")}</tbody></table></div></div>
      <div><h3>Top features by importance (gain share)</h3><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Feature</th><th>Column</th><th>Share</th></tr></thead>
        <tbody>${d.top_features.map((f) => row([esc(f.label), `<span class="mono">${esc(f.feature)}</span>`, `${(f.share * 100).toFixed(1)}%`])).join("")}</tbody></table></div></div>`;
  }

  return {
    update(s) { hSeg.set(s.h); load(s); },
    destroy() { bars.dispose(); lines.dispose(); },
  };
}
