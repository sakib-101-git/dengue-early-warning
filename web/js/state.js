// App state <-> URL. Every view is shareable and the back button works.
const KEYS = ["view", "city", "ward", "h", "date", "rain", "temp", "hum", "year", "scope", "stock", "crews", "floor", "cap", "basis", "effect", "deploy", "dscope", "tab", "dplan", "dmethod"];
const NUMERIC = new Set(["h", "rain", "temp", "hum", "year", "stock", "crews", "floor", "cap", "effect"]);
const VIEWS = ["ward", "city", "model", "resources", "hospitals", "simulator", "dispatch"];
// Resources defaults: a scarce-but-workable scenario so the allocation engine has something to do.
export const RES_DEFAULTS = { scope: "all", stock: 60, crews: 40, floor: 0 };
// Hospitals defaults: assumed capacity as derived (100%), planning for surge demand (upper end of the forecast).
export const HOSP_DEFAULTS = { cap: 100, basis: "surge" };
// Simulator defaults: 10% water-logging cut per crew, no crews deployed yet.
export const SIM_DEFAULTS = { effect: 10, deploy: "" };
export const MAX_CREWS_PER_WARD = 20;
// Dispatch defaults: a ward notice, SMS tab, crew impact of the crews Resources assigned.
// dmethod: "" = brief method page by scope (on for city, off for ward); "1" / "0" = the user's choice.
export const DISPATCH_DEFAULTS = { dscope: "ward", tab: "sms", dplan: "alloc", dmethod: "" };

/** "DNCC_Ward_01:2,DSCC_Ward_03:1" <-> { DNCC_Ward_01: 2, DSCC_Ward_03: 1 } (sorted, zeros dropped). */
export function decodeDeploy(text) {
  const out = {};
  for (const part of String(text || "").split(",")) {
    const [w, n] = part.split(":");
    const c = Math.round(Number(n));
    if (w && Number.isFinite(c) && c > 0) out[w.trim()] = Math.min(MAX_CREWS_PER_WARD, c);
  }
  return out;
}
export const encodeDeploy = (plan) =>
  Object.keys(plan).filter((w) => plan[w] > 0).sort().map((w) => `${w}:${plan[w]}`).join(",");

const listeners = new Set();
export const state = { view: "ward", city: "", ward: "", h: 3, date: "", rain: 0, temp: 0, hum: 0, year: 0, ...RES_DEFAULTS, ...HOSP_DEFAULTS, ...SIM_DEFAULTS, ...DISPATCH_DEFAULTS };
let meta = null;

export function initState(m) {
  meta = m;
  const d = m.default;
  Object.assign(state, { city: d.city, ward: d.ward, h: d.h, date: d.date });
  readUrl();
  history.replaceState(null, "", toUrl());
  window.addEventListener("popstate", () => { const prev = { ...state }; readUrl(); emit(prev); });
}

function readUrl() {
  const q = new URLSearchParams(location.search);
  for (const k of KEYS) {
    if (!q.has(k)) continue;
    const v = q.get(k);
    state[k] = NUMERIC.has(k) ? (Number.isFinite(Number(v)) ? Number(v) : 0) : v;
  }
  sanitize();
}

/** Keep state valid against /api/meta (unknown city, ward from another city, date out of range...). */
function sanitize() {
  if (!VIEWS.includes(state.view)) state.view = "ward";
  const cities = meta.cities.map((c) => c.name);
  if (!cities.includes(state.city)) state.city = meta.default.city;
  const wards = wardsOf(state.city);
  if (!wards.some((w) => w.id === state.ward)) state.ward = wards[0].id;
  if (!meta.horizons.includes(state.h)) state.h = meta.default.h;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(state.date || "")) state.date = meta.default.date;
  if (state.date < meta.min_date) state.date = meta.min_date;
  if (state.date > meta.max_date) state.date = meta.max_date;
  state.rain = clamp(state.rain, -80, 150);
  state.temp = clamp(state.temp, -4, 4);
  state.hum = clamp(state.hum, -20, 20);
  if (state.year && !meta.years.includes(state.year)) state.year = 0;
  if (state.scope !== "all" && !cities.includes(state.scope)) state.scope = RES_DEFAULTS.scope;
  state.stock = state.stock ? clamp(Math.round(state.stock), 30, 100) : RES_DEFAULTS.stock;
  state.crews = clamp(Math.round(state.crews), 0, 1000);
  state.floor = state.floor === 20 ? 20 : 0;
  state.cap = state.cap ? clamp(Math.round(state.cap / 5) * 5, 50, 200) : HOSP_DEFAULTS.cap;
  if (state.basis !== "expected" && state.basis !== "surge") state.basis = HOSP_DEFAULTS.basis;
  state.effect = state.effect ? clamp(Math.round(state.effect), 5, 20) : SIM_DEFAULTS.effect;
  const known = new Set(meta.cities.flatMap((c) => c.wards.map((w) => w.id)));
  const plan = decodeDeploy(state.deploy);
  const cityOf = Object.fromEntries(meta.cities.flatMap((c) => c.wards.map((w) => [w.id, c.name])));
  // crews only count inside the current scope, so the impact always matches the ranking shown
  for (const w of Object.keys(plan)) if (!known.has(w) || (state.scope !== "all" && cityOf[w] !== state.scope)) delete plan[w];
  state.deploy = encodeDeploy(plan);
  if (!["ward", "city"].includes(state.dscope)) state.dscope = DISPATCH_DEFAULTS.dscope;
  if (!["sms", "whatsapp", "brief"].includes(state.tab)) state.tab = DISPATCH_DEFAULTS.tab;
  if (!["alloc", "sim", "off"].includes(state.dplan)) state.dplan = DISPATCH_DEFAULTS.dplan;
  state.dmethod = ["0", "1"].includes(String(state.dmethod)) ? String(state.dmethod) : "";
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, Number(v) || 0));

export function wardsOf(city) {
  return (meta.cities.find((c) => c.name === city) || meta.cities[0]).wards;
}

function toUrl() {
  const q = new URLSearchParams();
  q.set("view", state.view);
  q.set("city", state.city);
  if (state.view === "ward" || (state.view === "dispatch" && state.dscope === "ward")) q.set("ward", state.ward);
  q.set("h", String(state.h));
  if (state.view !== "model") q.set("date", state.date);
  if (state.view === "ward") for (const k of ["rain", "temp", "hum"]) if (state[k]) q.set(k, String(state[k]));
  if (state.view === "city" && state.year) q.set("year", String(state.year));
  if (state.view === "resources") {
    q.set("scope", state.scope); q.set("stock", String(state.stock)); q.set("crews", String(state.crews));
    if (state.floor) q.set("floor", String(state.floor));
  }
  if (state.view === "hospitals") { q.set("scope", state.scope); q.set("cap", String(state.cap)); q.set("basis", state.basis); }
  if (state.view === "simulator") {
    q.set("scope", state.scope); q.set("effect", String(state.effect));
    if (state.deploy) q.set("deploy", state.deploy);
  }
  if (state.view === "dispatch") {
    q.set("dscope", state.dscope); q.set("tab", state.tab); q.set("dplan", state.dplan);
    if (state.dmethod) q.set("dmethod", state.dmethod);
    q.set("scope", state.scope); q.set("stock", String(state.stock)); q.set("crews", String(state.crews));
    if (state.floor) q.set("floor", String(state.floor));
    q.set("cap", String(state.cap)); q.set("basis", state.basis); q.set("effect", String(state.effect));
    if (state.deploy) q.set("deploy", state.deploy);
  }
  return `${location.pathname}?${q}`;
}

/**
 * Update state. `push` adds a history entry (view / place changes); sliders use replace so
 * the back button isn't flooded with every drag step.
 */
export function setState(patch, { push = true } = {}) {
  const prev = { ...state };
  Object.assign(state, patch);
  if (patch.city && patch.city !== prev.city && !patch.ward) state.ward = wardsOf(state.city)[0].id;
  sanitize();
  const changed = KEYS.some((k) => state[k] !== prev[k]);
  if (!changed) return;
  const url = toUrl();
  if (push) history.pushState(null, "", url); else history.replaceState(null, "", url);
  emit(prev);
}

export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
function emit(prev) { for (const fn of listeners) fn(state, prev); }
export function getMeta() { return meta; }
