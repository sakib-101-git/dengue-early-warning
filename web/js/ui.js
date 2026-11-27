// Small UI kit: formatting, count-up, segmented control, combobox, week stepper, popover sliders.
export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** "2025-07-28" -> Date at UTC midnight (no timezone drift). */
export const parseDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
export const isoDay = (dt) => dt.toISOString().slice(0, 10);
export const addDays = (s, n) => { const d = parseDay(s); d.setUTCDate(d.getUTCDate() + n); return isoDay(d); };
/** "28 Jul" or "28 Jul 2025" (portable: never uses %-d). */
export function day(s, withYear = false) {
  const d = parseDay(s);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}${withYear ? ` ${d.getUTCFullYear()}` : ""}`;
}
export const month = (s) => MONTHS[parseDay(s).getUTCMonth()];
export const fmt = (v, nd = 1) => (v === null || v === undefined ? "–" : Number(v).toFixed(nd));
export const pct = (v, nd = 0) => `${(v * 100).toFixed(nd)}%`;
export const signedPct = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(Math.round(v))}%`;

export const icon = {
  chev: `<svg class="chev" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6.5 9.5l5.5 5.5 5.5-5.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  left: `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M14.5 6.5L9 12l5.5 5.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  right: `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M9.5 6.5L15 12l-5.5 5.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  tune: `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M4 7h9M17 7h3M4 17h3M11 17h9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="15" cy="7" r="2.2" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="9" cy="17" r="2.2" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>`,
  info: `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M12 11v5.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="7.7" r="1.05" fill="currentColor"/></svg>`,
  alert: `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="12" cy="12" r="9.2" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M12 7.5v5.5" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><circle cx="12" cy="16.4" r="1.1" fill="currentColor"/></svg>`,
  reset: `<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3M4.5 4.5v3.8h3.8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  arrow: `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M5 12h13M13 6.5l5.5 5.5-5.5 5.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
};

// ---- info tooltip: one per card. Keyboard-focusable button, described by its tooltip; hover / focus /
// tap to open, Esc or a click outside closes. Flips to stay inside the viewport.
// Info tooltips. The tip is `hidden` in the markup and only JS reveals it, so even without the
// stylesheet no explanation text shows inline. Shown while hovered (mouse), keyboard-focused or
// opened by click/tap; Esc dismisses it, a click or tap outside closes it.
let tipSeq = 0;
export const info = (text, label = "More information") => {
  const id = `tip${++tipSeq}`;
  return `<span class="info"><button type="button" class="info__btn" aria-label="${esc(label)}" aria-describedby="${id}">${icon.info}</button>` +
    `<span class="info__tip" role="tooltip" id="${id}" hidden>${esc(text)}</span></span>`;
};

const canHover = matchMedia("(hover: hover)");
function syncTip(box) {
  const tip = box.querySelector(".info__tip");
  if (!tip) return;
  const c = box.classList;
  const show = (c.contains("is-open") || c.contains("is-hover") || c.contains("is-focus")) && !c.contains("is-dismissed");
  if (show === !tip.hidden) return;
  tip.hidden = !show;
  if (!show) return;
  c.remove("info--l", "info--up");                     // keep the tip on screen
  const r = tip.getBoundingClientRect();
  if (r.left < 8) c.add("info--l");
  if (r.bottom > innerHeight - 8 && r.height + 40 < box.getBoundingClientRect().top) c.add("info--up");
}
const setTip = (box, cls, on) => { box.classList.toggle(cls, on); syncTip(box); };
const closeTips = (except) => document.querySelectorAll(".info.is-open").forEach((b) => { if (b !== except) setTip(b, "is-open", false); });
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".info__btn");
  if (!btn) return;
  e.preventDefault();                                  // never activate a surrounding <label>
  const box = btn.parentElement;
  closeTips(box);
  box.classList.remove("is-dismissed");
  setTip(box, "is-open", !box.classList.contains("is-open"));
});
document.addEventListener("pointerdown", (e) => { if (!e.target.closest(".info")) closeTips(); }, true);
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  document.querySelectorAll(".info.is-open, .info.is-hover, .info.is-focus").forEach((b) => {
    b.classList.remove("is-open"); setTip(b, "is-dismissed", true);
  });
});
document.addEventListener("mouseover", (e) => {
  const b = e.target.closest?.(".info");
  if (b && canHover.matches && !b.contains(e.relatedTarget)) setTip(b, "is-hover", true);
});
document.addEventListener("mouseout", (e) => {
  const b = e.target.closest?.(".info");
  if (b && !b.contains(e.relatedTarget)) { b.classList.remove("is-dismissed"); setTip(b, "is-hover", false); }
});
document.addEventListener("focusin", (e) => {
  const btn = e.target.closest?.(".info__btn");
  if (btn && btn.matches(":focus-visible")) setTip(btn.parentElement, "is-focus", true);
});
document.addEventListener("focusout", (e) => {
  const b = e.target.closest?.(".info");
  if (b) { b.classList.remove("is-dismissed", "is-open"); setTip(b, "is-focus", false); }
});

export function cardHead(title, sub = "", tip = "", right = "") {
  return `<div class="card__head"><div><h2>${esc(title)}</h2>${sub ? `<p>${esc(sub)}</p>` : ""}</div>` +
    `<div class="right">${right}${tip ? info(tip) : ""}</div></div>`;
}

export function errorBox(msg, onRetry) {
  const el = document.createElement("div");
  el.className = "error";
  el.setAttribute("role", "alert");
  el.innerHTML = `${icon.alert}<div><b>Couldn't load this view</b><span>${esc(msg)}</span></div>` +
    `<button class="btn btn--sm" type="button">Retry</button>`;
  el.querySelector("button").addEventListener("click", onRetry);
  return el;
}

/** Animate a number from its previous value to `to`. */
export function countUp(el, to, { decimals = 1, duration = 900, suffix = "", prefix = "" } = {}) {
  if (!el) return;
  const from = el._v ?? 0;
  el._v = to;
  const render = (v) => { el.textContent = `${prefix}${v.toFixed(decimals)}${suffix}`; };
  cancelAnimationFrame(el._raf);
  if (reducedMotion() || from === to) { render(to); return; }
  const t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / duration);
    const e = 1 - Math.pow(1 - k, 4);  // easeOutQuart
    render(from + (to - from) * e);
    if (k < 1) el._raf = requestAnimationFrame(step);
  };
  el._raf = requestAnimationFrame(step);
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ------------------------------------------------------------------ segmented control (sliding thumb)
export function placeThumb(seg) {
  const thumb = seg.querySelector(".seg__thumb");
  const on = seg.querySelector('[aria-checked="true"], [aria-current="page"]');
  if (!thumb || !on) return;
  thumb.style.width = `${on.offsetWidth}px`;
  thumb.style.transform = `translateX(${on.offsetLeft}px)`;
  requestAnimationFrame(() => seg.classList.add("is-ready"));
}
const segObserver = new ResizeObserver((entries) => entries.forEach((e) => placeThumb(e.target)));
export const watchSeg = (seg) => { segObserver.observe(seg); placeThumb(seg); };

export function segmented({ label, items, value, onChange }) {
  const el = document.createElement("div");
  el.className = "seg seg--ctl";
  el.setAttribute("role", "radiogroup");
  el.setAttribute("aria-label", label);
  el.innerHTML = `<span class="seg__thumb" aria-hidden="true"></span>` + items.map((it) =>
    `<button type="button" class="seg__item" role="radio" data-v="${esc(it.value)}">${esc(it.label)}</button>`).join("");
  const btns = [...el.querySelectorAll(".seg__item")];
  const set = (v) => {
    btns.forEach((b) => { const on = b.dataset.v === String(v); b.setAttribute("aria-checked", on); b.tabIndex = on ? 0 : -1; });
    placeThumb(el);
  };
  el.addEventListener("click", (e) => { const b = e.target.closest(".seg__item"); if (b) onChange(b.dataset.v); });
  el.addEventListener("keydown", (e) => {
    const i = btns.findIndex((b) => b.getAttribute("aria-checked") === "true");
    const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!d) return;
    e.preventDefault();
    const nb = btns[(i + d + btns.length) % btns.length];
    nb.focus();
    onChange(nb.dataset.v);
  });
  set(value);
  watchSeg(el);
  return { el, set };
}

// ------------------------------------------------------------------ combobox with optional search
let comboSeq = 0;
export function combobox({ label, options, value, search = false, onChange, placeholder = "Search" }) {
  const id = `cb${++comboSeq}`;
  const el = document.createElement("div");
  el.className = "combo";
  el.innerHTML =
    `<button type="button" class="ctl" id="${id}" aria-haspopup="listbox" aria-expanded="false" aria-controls="${id}-list" aria-label="${esc(label)}">` +
    `<span class="ctl__val"></span>${icon.chev}</button>` +
    `<div class="menu" hidden>${search ? `<input class="menu__search" type="text" placeholder="${esc(placeholder)}" aria-label="Filter ${esc(label)}" autocomplete="off" spellcheck="false">` : ""}` +
    `<ul class="menu__list" role="listbox" id="${id}-list" aria-label="${esc(label)}" tabindex="-1"></ul></div>`;
  const btn = el.querySelector(".ctl");
  const val = el.querySelector(".ctl__val");
  const menu = el.querySelector(".menu");
  const list = el.querySelector(".menu__list");
  const input = el.querySelector(".menu__search");
  let opts = options, cur = value, shown = [], active = 0;

  const render = () => {
    const q = (input?.value || "").trim().toLowerCase();
    shown = opts.filter((o) => !q || o.label.toLowerCase().includes(q) || String(o.value).toLowerCase().includes(q));
    list.innerHTML = shown.length ? shown.map((o, i) =>
      `<li class="menu__opt${o.tier ? ` tier-${o.tier}` : ""}${i === active ? " is-active" : ""}" role="option" id="${id}-o${i}" aria-selected="${o.value === cur}" data-i="${i}">` +
      `${o.tier ? '<i class="dot" aria-hidden="true"></i>' : ""}${esc(o.label)}${o.hint ? `<small>${esc(o.hint)}</small>` : ""}</li>`).join("")
      : `<li class="menu__empty">No matches</li>`;
    btn.setAttribute("aria-activedescendant", shown.length ? `${id}-o${active}` : "");
    list.querySelector(".is-active")?.scrollIntoView({ block: "nearest" });
  };
  const open = () => {
    if (!menu.hidden) return;
    menu.hidden = false; btn.setAttribute("aria-expanded", "true");
    if (input) input.value = "";
    active = Math.max(0, opts.findIndex((o) => o.value === cur));
    render();
    (input || btn).focus();
    document.addEventListener("pointerdown", outside, true);
  };
  const close = (focus = true) => {
    if (menu.hidden) return;
    menu.hidden = true; btn.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", outside, true);
    if (focus) btn.focus();
  };
  const outside = (e) => { if (!el.contains(e.target)) close(false); };
  const pick = (i) => { const o = shown[i]; if (!o) return; close(); if (o.value !== cur) onChange(o.value); };
  const keys = (e) => {
    if (menu.hidden) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); open(); }
      return;
    }
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(shown.length - 1, active + 1); render(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(0, active - 1); render(); }
    else if (e.key === "Home") { e.preventDefault(); active = 0; render(); }
    else if (e.key === "End") { e.preventDefault(); active = shown.length - 1; render(); }
    else if (e.key === "Enter") { e.preventDefault(); pick(active); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "Tab") close(false);
  };
  btn.addEventListener("click", () => (menu.hidden ? open() : close()));
  btn.addEventListener("keydown", keys);
  input?.addEventListener("keydown", keys);
  input?.addEventListener("input", () => { active = 0; render(); });
  list.addEventListener("click", (e) => { const li = e.target.closest("[data-i]"); if (li) pick(Number(li.dataset.i)); });
  list.addEventListener("pointermove", (e) => {
    const li = e.target.closest("[data-i]"); if (!li || Number(li.dataset.i) === active) return;
    active = Number(li.dataset.i); list.querySelectorAll(".is-active").forEach((n) => n.classList.remove("is-active")); li.classList.add("is-active");
  });
  const set = (v, newOpts) => {
    if (newOpts) opts = newOpts;
    cur = v;
    const o = opts.find((x) => x.value === v);
    val.textContent = o ? o.label : "";
  };
  set(value);
  return { el, set, btnId: id };
}

// ------------------------------------------------------------------ week stepper (‹ date ›)
export function weekStepper({ value, min, max, onChange, label = "Data up to" }) {
  const el = document.createElement("div");
  el.className = "stepper";
  el.innerHTML =
    `<button type="button" data-d="-7" aria-label="Previous week">${icon.left}</button>` +
    `<input type="date" aria-label="${esc(label)}" min="${min}" max="${max}" required>` +
    `<button type="button" data-d="7" aria-label="Next week">${icon.right}</button>`;
  const input = el.querySelector("input");
  const [prev, next] = el.querySelectorAll("button");
  const clampDay = (s) => (s < min ? min : s > max ? max : s);
  const set = (v) => { input.value = v; prev.disabled = v <= min; next.disabled = v >= max; };
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-d]"); if (!b) return;
    const v = clampDay(addDays(input.value || value, Number(b.dataset.d)));
    set(v); onChange(v);
  });
  input.addEventListener("change", () => { if (/^\d{4}-\d{2}-\d{2}$/.test(input.value)) { const v = clampDay(input.value); set(v); onChange(v); } });
  set(value);
  return { el, set };
}

// ------------------------------------------------------------------ simulate-weather popover
const SLIDERS = [
  { key: "rain", label: "Rain, last 4 weeks", min: -80, max: 150, step: 10, fmt: (v) => `${v > 0 ? "+" : ""}${v}%`, ends: ["−80%", "+150%"] },
  { key: "temp", label: "Temperature", min: -4, max: 4, step: 1, fmt: (v) => `${v > 0 ? "+" : ""}${v} °C`, ends: ["−4 °C", "+4 °C"] },
  { key: "hum", label: "Humidity", min: -20, max: 20, step: 2, fmt: (v) => `${v > 0 ? "+" : ""}${v} pts`, ends: ["−20", "+20"] },
];

export function simPopover({ values, onInput, onReset }) {
  const el = document.createElement("div");
  el.className = "pop";
  el.innerHTML =
    `<button type="button" class="btn" aria-haspopup="dialog" aria-expanded="false">${icon.tune}<span>Change weather</span><span class="badge" hidden></span></button>` +
    `<div class="pop__panel" role="dialog" aria-label="Change weather" hidden>` +
    `<h3>Change the last 4 weeks</h3>` +
    SLIDERS.map((s) =>
      `<div class="slider"><div class="slider__top"><label for="sim-${s.key}">${s.label}</label><output for="sim-${s.key}"></output></div>` +
      `<input id="sim-${s.key}" type="range" min="${s.min}" max="${s.max}" step="${s.step}" data-k="${s.key}">` +
      `<div class="slider__ends" aria-hidden="true"><span>${s.ends[0]}</span><span>${s.ends[1]}</span></div></div>`).join("") +
    `<div class="pop__foot"><span></span><button type="button" class="btn btn--sm" data-reset>${icon.reset}Reset weather</button></div></div>`;
  const btn = el.querySelector(".btn");
  const panel = el.querySelector(".pop__panel");
  const badge = el.querySelector(".badge");
  const inputs = [...el.querySelectorAll('input[type="range"]')];

  const paint = (inp) => {
    const s = SLIDERS.find((x) => x.key === inp.dataset.k);
    const v = Number(inp.value);
    const p = ((v - s.min) / (s.max - s.min)) * 100;
    const z = ((0 - s.min) / (s.max - s.min)) * 100;
    inp.style.setProperty("--p", `${p}%`);
    inp.style.setProperty("--z", `${z}%`);
    const out = inp.closest(".slider").querySelector("output");
    out.textContent = v === 0 ? "as recorded" : s.fmt(v);
    out.classList.toggle("is-on", v !== 0);
    inp.setAttribute("aria-valuetext", out.textContent);
  };
  const set = (vals) => {
    inputs.forEach((inp) => { inp.value = vals[inp.dataset.k] ?? 0; paint(inp); });
    const n = inputs.filter((i) => Number(i.value) !== 0).length;
    btn.classList.toggle("is-on", n > 0);
    badge.hidden = n === 0; badge.textContent = n;
  };
  const open = () => { panel.hidden = false; btn.setAttribute("aria-expanded", "true"); inputs[0].focus(); document.addEventListener("pointerdown", outside, true); };
  const close = (focus = true) => { if (panel.hidden) return; panel.hidden = true; btn.setAttribute("aria-expanded", "false"); document.removeEventListener("pointerdown", outside, true); if (focus) btn.focus(); };
  const outside = (e) => { if (!el.contains(e.target)) close(false); };

  btn.addEventListener("click", () => (panel.hidden ? open() : close()));
  panel.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); close(); } });
  inputs.forEach((inp) => inp.addEventListener("input", () => {
    paint(inp);
    const vals = Object.fromEntries(inputs.map((i) => [i.dataset.k, Number(i.value)]));
    set(vals);
    onInput(vals);
  }));
  el.querySelector("[data-reset]").addEventListener("click", () => { set({ rain: 0, temp: 0, hum: 0 }); onReset(); });
  set(values);
  return { el, set, open, close };
}

export function field(labelText, control, { cls = "", tip = "", forId = "" } = {}) {
  const f = document.createElement("div");
  f.className = `field ${cls}`;
  const lab = document.createElement(forId ? "label" : "div");
  lab.className = forId ? "" : "label";
  if (forId) lab.htmlFor = forId;
  lab.innerHTML = `${esc(labelText)}${tip ? info(tip) : ""}`;
  f.append(lab, control);
  return f;
}
