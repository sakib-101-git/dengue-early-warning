// Dengue Watch: app shell. Boots from /api/meta, keeps the view in sync with the URL.
import { api } from "./api.js";
import { disposeAll, rethemeAll } from "./charts.js";
import { getMeta, initState, setState, state, subscribe } from "./state.js";
import { errorBox, placeThumb, reducedMotion, watchSeg } from "./ui.js";
import { mountCity } from "./views/city.js";
import { mountModel } from "./views/model.js";
import { mountHospitals } from "./views/hospitals.js";
import { mountResources } from "./views/resources.js";
import { mountSimulator } from "./views/simulator.js";
import { mountWard } from "./views/ward.js";

const VIEWS = { ward: mountWard, city: mountCity, model: mountModel, resources: mountResources, hospitals: mountHospitals, simulator: mountSimulator };
const viewEl = document.getElementById("view");
const nav = document.querySelector(".seg--nav");
let current = null, currentName = "";

// ------------------------------------------------------------------ theme
const themeBtn = document.querySelector(".theme-toggle");
function applyTheme(t, persist) {
  document.documentElement.dataset.theme = t;
  themeBtn.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme");
  if (persist) { try { localStorage.setItem("dw-theme", t); } catch { /* private mode */ } }
  applyTierColors();
  rethemeAll();
}
themeBtn.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  if (document.startViewTransition && !reducedMotion()) document.startViewTransition(() => applyTheme(next, true));
  else applyTheme(next, true);
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
  let saved = null;
  try { saved = localStorage.getItem("dw-theme"); } catch { /* ignore */ }
  if (!saved) applyTheme(e.matches ? "dark" : "light", false);
});

/** Tier colours come from the API so the UI and model agree on one palette. */
function applyTierColors() {
  const m = getMeta();
  if (!m) return;
  const mode = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  for (const [t, c] of Object.entries(m.tier_colors)) document.documentElement.style.setProperty(`--t-${t}`, c[mode]);
}

// ------------------------------------------------------------------ navigation
nav.addEventListener("click", (e) => {
  const a = e.target.closest("[data-nav]");
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey) return;
  e.preventDefault();
  setState({ view: a.dataset.nav });
});
document.querySelector(".brand").addEventListener("click", (e) => {
  if (e.metaKey || e.ctrlKey) return;
  e.preventDefault();
  setState({ view: "ward" });
});

function markNav(view) {
  nav.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  placeThumb(nav);
  const on = nav.querySelector('[aria-current="page"]');  // keep the active tab visible when the nav scrolls (phones)
  if (on && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = on.offsetLeft - (nav.clientWidth - on.offsetWidth) / 2;
  document.title = `${{ ward: "Ward", city: "City", model: "Model", resources: "Resources", hospitals: "Hospitals", simulator: "Simulator" }[view]} · Dengue Watch`;
}

function swapView(s, prev) {
  const go = () => {
    current?.destroy?.();
    disposeAll();
    viewEl.innerHTML = "";
    currentName = s.view;
    current = VIEWS[s.view](viewEl, s);
    current.update(s, null);
    if (!reducedMotion()) {
      viewEl.classList.remove("is-entering");
      void viewEl.offsetWidth;
      viewEl.classList.add("is-entering");
      setTimeout(() => viewEl.classList.remove("is-entering"), 900);
    }
  };
  markNav(s.view);
  if (prev && document.startViewTransition && !reducedMotion()) document.startViewTransition(go);
  else go();
  if (prev) window.scrollTo({ top: 0, behavior: reducedMotion() ? "auto" : "smooth" });
}

function onState(s, prev) {
  if (s.view !== currentName) swapView(s, prev);
  else current.update(s, prev);
}

// ------------------------------------------------------------------ boot
async function boot() {
  viewEl.innerHTML = `<div class="pagehead"><div><div class="skel skel--line" style="width:120px"></div><div class="skel skel--big" style="width:240px;height:40px"></div></div></div>
    <div class="card"><div class="skel skel--block" style="height:300px"></div></div>`;
  let meta;
  try {
    meta = await api("meta");
  } catch (e) {
    viewEl.replaceChildren(errorBox(e.message, boot));
    return;
  }
  initState(meta);
  applyTheme(document.documentElement.dataset.theme || "light", false);
  watchSeg(nav);
  subscribe(onState);
  swapView(state, null);
}

if (window.echarts) boot();
else window.addEventListener("load", boot, { once: true });
