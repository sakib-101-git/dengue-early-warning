// Dispatch: ward / city notices as SMS, WhatsApp and a printable commissioner brief.
// Every figure comes from /api/dispatch, which reads the same payloads as Resources, Hospitals and Simulator.
import { api, isAbort } from "../api.js";
import { getMeta, setState, wardsOf } from "../state.js";
import { combobox, day, errorBox, esc, field, info, segmented, weekStepper } from "../ui.js";

const TABS = [{ value: "sms", label: "SMS" }, { value: "whatsapp", label: "WhatsApp" }, { value: "brief", label: "Brief" }];
const PLANS = [{ value: "alloc", label: "Allocated crews" }, { value: "sim", label: "Simulator plan" }, { value: "off", label: "Off" }];

// ---- GSM-7 counter (same rules as src/dispatch.py: 160 / 153 septets, ext chars count 2; UCS-2 70 / 67)
const GSM_BASIC = new Set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà");
const GSM_EXT = new Set("^{}\\[~]|€\f");
export function smsInfo(text) {
  const chars = [...text];
  const gsm = chars.every((c) => GSM_BASIC.has(c) || GSM_EXT.has(c));
  const units = gsm ? chars.reduce((n, c) => n + (GSM_EXT.has(c) ? 2 : 1), 0) : text.length;  // UTF-16 units
  const [single, multi] = gsm ? [160, 153] : [70, 67];
  const segments = units <= single ? 1 : Math.ceil(units / multi);
  const limit = (segments === 1 ? single : multi) * segments;
  const bad = gsm ? [] : [...new Set(chars.filter((c) => !GSM_BASIC.has(c) && !GSM_EXT.has(c)))];
  return { encoding: gsm ? "GSM-7" : "UCS-2", units, segments, limit, remaining: limit - units, bad };
}

/** WhatsApp-style preview: *bold* -> <b>, _italic_ -> <i>, lines kept. */
const waHtml = (t) => esc(t).replace(/\*([^*\n]+)\*/g, "<b>$1</b>").replace(/(^|\n)_([^_\n]+)_(?=\n|$)/g, "$1<i>$2</i>");

// Plain-language numbers, same rules as src/dispatch.py: whole cases, "fewer than 1" below 1.
const whole = (x) => Math.floor(Number(x) + 0.5);
const rangeWord = (lo, hi) => (whole(hi) < 1 ? "" : whole(lo) < 1 ? `up to ${whole(hi)}` : whole(lo) === whole(hi) ? "" : `likely ${whole(lo)} to ${whole(hi)}`);
const SUPPLY_NAME = { saline: "Saline bags", kits: "Test kits", beds: "Extra (surge) beds" };

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* insecure context / denied */ }
  const ta = Object.assign(document.createElement("textarea"), { value: text });
  ta.style.cssText = "position:fixed;opacity:0;top:0;left:0";
  document.body.append(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  return ok;
}

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function mountDispatch(root, st) {
  const meta = getMeta();
  root.innerHTML = `
    <header class="pagehead"><div>
      <div class="crumbs"><span>Dispatch</span></div>
      <h1 data-title>&nbsp;</h1>
      <p data-sub>&nbsp;</p>
    </div></header>
    <section class="toolbar dsp-toolbar" aria-label="Notice settings"></section>
    <section class="card dsp-settings" aria-label="Plan settings used in this notice">
      <div class="dsp-settings__row" data-settings></div>
      <div class="field dsp-plan" data-plan></div>
    </section>
    <div class="dsp-split">
      <section class="card dsp-main" aria-labelledby="dsp-t">
        <div class="card__head"><div><h2 id="dsp-t">Notice</h2><p data-tab-sub></p></div>
          <div class="right"><span data-tabs></span>${info("Built offline from fixed templates, no AI. SMS aims for 160 characters; for a PDF use Print brief, then Save as PDF.")}</div></div>
        <div data-panel><div class="skel skel--block" style="height:260px"></div></div>
      </section>
      <aside class="card dsp-side" aria-labelledby="dsp-s">
        <div class="card__head"><div><h2 id="dsp-s">Summary</h2></div></div>
        <div data-side></div>
      </aside>
    </div>`;

  // ---- toolbar
  const bar = root.querySelector(".toolbar");
  const scopeSeg = segmented({ label: "Notice for", items: [{ value: "ward", label: "Ward" }, { value: "city", label: "City" }],
    value: st.dscope, onChange: (v) => setState({ dscope: v }) });
  const cityBox = combobox({ label: "City", options: meta.cities.map((c) => ({ value: c.name, label: c.name, hint: `${c.wards.length} wards` })),
    value: st.city, onChange: (v) => setState({ city: v }) });
  const wardOpts = (c) => wardsOf(c).map((w) => ({ value: w.id, label: w.label }));
  const wardBox = combobox({ label: "Ward", options: wardOpts(st.city), value: st.ward, search: true, placeholder: "Search wards",
    onChange: (v) => setState({ ward: v }) });
  const hSeg = segmented({ label: "Weeks ahead", items: meta.horizons.map((h) => ({ value: h, label: `${h} weeks` })), value: st.h,
    onChange: (v) => setState({ h: Number(v) }) });
  const stepper = weekStepper({ value: st.date, min: meta.min_date, max: meta.max_date, onChange: (v) => setState({ date: v }) });
  const wardField = field("Ward", wardBox.el);
  bar.append(field("Notice for", scopeSeg.el), field("City", cityBox.el), wardField,
             field("Weeks ahead", hSeg.el, { cls: "field--h" }), field("Data up to", stepper.el, { cls: "field--date" }));

  const planSeg = segmented({ label: "Crew impact", items: PLANS, value: st.dplan, onChange: (v) => setState({ dplan: v }, { push: false }) });
  root.querySelector("[data-plan]").append(
    Object.assign(document.createElement("div"), { className: "label", innerHTML: `Crew impact${info("Adds the Simulator's model estimate of cases prevented over 2–4 weeks, for the crews Resources assigned or your Simulator plan.")}` }),
    planSeg.el);

  const tabSeg = segmented({ label: "Channel", items: TABS, value: st.tab, onChange: (v) => setState({ tab: v }, { push: false }) });
  tabSeg.el.classList.add("dsp-tabs");
  root.querySelector("[data-tabs]").append(tabSeg.el);

  // ---- data
  let data = null, lastKey = "", loaded = false, smsDraft = null, cur = st;
  const methodOn = (s) => (s.dmethod ? s.dmethod === "1" : s.dscope === "city");
  const params = (s) => {
    const plan = s.dplan === "alloc" ? "allocation" : s.dplan === "sim" ? s.deploy : "";
    return { scope: s.dscope, id: s.dscope === "ward" ? s.ward : s.city, date: s.date, h: s.h, stock_pct: s.stock, crews: s.crews,
      floor: s.floor, capacity_pct: s.cap, basis: s.basis, pool: s.scope === "all" ? "all" : "city", plan, effect: s.effect,
      method: methodOn(s) ? 1 : 0, format: "json" };
  };

  async function load(s) {
    const p = params(s);
    const key = JSON.stringify(p);
    if (key === lastKey) { renderPanel(s); return; }
    lastKey = key;
    if (loaded) root.classList.add("is-busy");
    try {
      data = await api("dispatch", p, "dispatch");
      smsDraft = null;
      render(s);
      loaded = true;
    } catch (e) {
      if (isAbort(e)) return;
      lastKey = "";
      root.querySelector("[data-panel]").replaceChildren(errorBox(e.message, () => load(s)));
    } finally { root.classList.remove("is-busy"); }
  }

  function render(s) {
    const c = data.context;
    const ward = data.scope === "ward";
    root.querySelector("[data-title]").textContent = ward ? `${c.name}` : `${c.city} brief`;
    root.querySelector("[data-sub]").innerHTML = `${ward ? `${esc(c.city)} · ` : ""}Week of <b>${day(c.target_date, true)}</b>`;
    const se = c.settings;
    const chip = (label, val, view, title) =>
      `<a class="dsp-chip" href="?view=${view}" data-go="${view}" title="${esc(title)}"><span>${esc(label)}</span><b>${esc(val)}</b></a>`;
    root.querySelector("[data-settings]").innerHTML =
      chip("Stock", `${se.stock_pct}% · ${se.pool}`, "resources", "Available regional stock and pool. Change it in Resources.") +
      chip("Crews pool", `${se.crews_total}${se.floor_pct ? ` · floor ${se.floor_pct}%` : ""}`, "resources", "Total spraying crews. Change it in Resources.") +
      chip("Beds", `${se.capacity_pct}% of regular beds · ${se.basis === "surge" ? "bad week" : "normal week"}`, "hospitals", "Bed capacity scale, and whether beds are planned for a bad week. Change it in Hospitals.") +
      chip("Crew impact", data.plan === "none" ? "off" : data.plan === "allocation" ? `allocated crews · ${se.sim?.effect_pct ?? s.effect}%` : `simulator plan · ${se.sim?.crews ?? 0} crews`,
        "simulator", "Model counterfactual of the crews. Build a plan in Simulator.");
    renderSide(c);
    renderPanel(s);
  }

  // Compact summary only: the full story (alerts, beds, crews, actions) is in the notice itself.
  function renderSide(c) {
    const ward = data.scope === "ward";
    const flags = ward ? [...new Map(c.alerts.map((a) => [a.label, a.level])).entries()]
      : (() => { const n = c.wards.filter((w) => w.alerts.length).length; return n ? [[`${n} ward${n === 1 ? "" : "s"} short`, "WARNING"]] : []; })();
    const flagHtml = flags.length
      ? flags.map(([label, level]) => `<span class="flag ${level === "CRITICAL DEFICIT" ? "flag--crit" : "flag--warn"}">${esc(label)}</span>`).join("")
      : '<span class="flag flag--ok">Covered</span>';
    const rng = ward ? rangeWord(c.low_80, c.high_80) : "";
    const range = rng ? ` <span class="sim-muted">${rng}</span>` : "";
    const mini = c.supplies.map((s) => `<div class="dsp-mini__row"><span>${SUPPLY_NAME[s.key]}</span>
        <span class="num">${s.required}</span><span class="num">${s.allocated}</span>
        <span class="num${s.deficit ? " txt-crit" : ""}">${s.deficit || "–"}</span></div>`).join("");
    root.querySelector("[data-side]").innerHTML = `<div class="dsp-sum">
      <div class="dsp-sum__flags">${ward ? `<span class="tier tier-${c.tier}">${c.tier} risk</span>` : ""}${flagHtml}</div>
      <dl class="dsp-sum__kv">
        <dt>Cases</dt><dd><b class="fig">${c.expected < 1 ? "&lt;1" : whole(c.expected)}</b>${range}${ward ? "" : ` <span class="sim-muted">in ${c.n_wards} wards</span>`}</dd>
        <dt>Act by</dt><dd>${day(c.deadline, true)}</dd>
      </dl>
      <div class="dsp-mini" role="table" aria-label="Supplies">
        <div class="dsp-mini__row dsp-mini__head" role="row"><span></span><span>Need</span><span>Getting</span><span>Short</span></div>
        ${mini}
      </div>
    </div>`;
  }

  function counter(text) {
    const i = smsInfo(text);
    const over = i.segments > 2;
    return `<span class="num"><b>${i.units}</b>/${i.limit}</span> characters · <b class="${over ? "txt-crit" : ""}">${i.segments} segment${i.segments === 1 ? "" : "s"}</b> · ${i.encoding}` +
      (i.bad.length ? ` <span class="txt-crit">(non-GSM: ${esc(i.bad.slice(0, 4).join(" "))})</span>` : "") +
      (over ? ' <span class="txt-crit">over the 2-segment budget</span>' : "");
  }

  function renderPanel(s) {
    if (!data) return;
    const box = root.querySelector("[data-panel]");
    const files = data.files;
    const btns = (extra = "") => `<div class="dsp-actions">${extra}<button type="button" class="btn btn--sm" data-copy>Copy text</button>` +
      `<button type="button" class="btn btn--sm" data-dl>Download .${s.tab === "brief" ? "html" : "txt"}</button><span class="dsp-done" role="status" aria-live="polite"></span></div>`;
    const sub = root.querySelector("[data-tab-sub]");
    if (s.tab === "sms") {
      sub.textContent = "Editable before sending";
      const text = smsDraft ?? data.sms;
      box.innerHTML = `<div class="dsp-phone"><label class="sr-only" for="dsp-sms">SMS text</label>
        <textarea id="dsp-sms" class="dsp-bubble dsp-bubble--sms" rows="6" spellcheck="false">${esc(text)}</textarea></div>
        <p class="dsp-count" data-count>${counter(text)}</p>` + btns(smsDraft !== null ? '<button type="button" class="btn btn--sm" data-reset>Undo edits</button>' : "");
      const ta = box.querySelector("textarea");
      ta.addEventListener("input", () => {
        smsDraft = ta.value === data.sms ? null : ta.value;
        box.querySelector("[data-count]").innerHTML = counter(ta.value);
      });
    } else if (s.tab === "whatsapp") {
      sub.textContent = "";
      box.innerHTML = `<div class="dsp-phone dsp-phone--wa"><div class="dsp-bubble dsp-bubble--wa">${waHtml(data.whatsapp)}</div></div>` + btns();
    } else {
      sub.textContent = data.scope === "ward" ? "A4 · one page" : "A4 · city brief";
      box.innerHTML = `<iframe class="dsp-brief" title="Brief preview"></iframe>` +
        btns('<button type="button" class="btn btn--sm btn--primary" data-print>Print brief</button>' +
          `<label class="dsp-check"><input type="checkbox" data-method${methodOn(s) ? " checked" : ""}> Include method page</label>`);
      box.querySelector("iframe").srcdoc = data.brief;
    }
    box.dataset.file = files[s.tab];
  }

  root.addEventListener("click", async (e) => {
    const go = e.target.closest("[data-go]");
    if (go && !e.metaKey && !e.ctrlKey) { e.preventDefault(); setState({ view: go.dataset.go }); return; }
    if (!data) return;
    const done = (msg) => { const el = root.querySelector(".dsp-done"); if (el) { el.textContent = msg; setTimeout(() => { el.textContent = ""; }, 1800); } };
    const tab = cur.tab;
    const text = tab === "sms" ? (smsDraft ?? data.sms) : tab === "whatsapp" ? data.whatsapp : data.brief;
    if (e.target.closest("[data-copy]")) done((await copyText(text)) ? "Copied" : "Copy failed: select the text and copy it");
    else if (e.target.closest("[data-dl]")) { download(data.files[tab], text, tab === "brief" ? "text/html" : "text/plain"); done("Downloaded"); }
    else if (e.target.closest("[data-reset]")) { smsDraft = null; renderPanel(cur); }
    else if (e.target.closest("[data-print]")) {
      const fr = root.querySelector(".dsp-brief");
      try { fr.contentWindow.focus(); fr.contentWindow.print(); } catch { done("Use Download, then print from the browser"); }
    }
  });

  root.addEventListener("change", (e) => {
    const m = e.target.closest("[data-method]");
    if (m) setState({ dmethod: m.checked ? "1" : "0" }, { push: false });
  });

  return {
    update(s) {
      cur = s;
      scopeSeg.set(s.dscope); hSeg.set(s.h); stepper.set(s.date); tabSeg.set(s.tab);
      cityBox.set(s.city); wardBox.set(s.ward, wardOpts(s.city));
      wardField.hidden = s.dscope !== "ward";
      const canSim = Boolean(s.deploy);
      planSeg.el.querySelector('[data-v="sim"]').disabled = !canSim;
      planSeg.el.querySelector('[data-v="sim"]').title = canSim ? "" : "Build a crew plan in Simulator first";
      planSeg.set(s.dplan === "sim" && !canSim ? "off" : s.dplan);
      load(s.dplan === "sim" && !canSim ? { ...s, dplan: "off" } : s);
    },
    destroy() {},
  };
}
