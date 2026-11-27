// Header status chip: is the Dengue Watch server reachable, and can it work with no internet?
// Combines navigator.onLine (the browser's own connection) with a ping of /api/health, whose
// offline_ready flag says that models, data, supply rates and vendored assets are all loaded locally.
const PING_MS = 30000;
const TIMEOUT_MS = 4000;

const STATES = {
  checking: { text: "Checking", tip: "Checking the server…" },
  ready: { text: "Offline-ready", tip: "Offline-ready: forecasts, allocation, routing, simulator and dispatch all run on this server, with no internet needed." },
  readyNoNet: { text: "Offline-ready", tip: "No internet connection, and nothing is lost: forecasts, allocation, routing, simulator and dispatch all run on this server." },
  degraded: { text: "Degraded", tip: "The server is up, but some parts did not load: " },
  down: { text: "Server unreachable", tip: "Cannot reach the Dengue Watch server. Check that it is running, then reload." },
};

export function mountNetChip(el) {
  if (!el) return;
  const txt = el.querySelector(".net__txt");
  let timer = 0, inflight = null;

  function show(key, extra = "") {
    const s = STATES[key];
    el.className = `net is-${key === "readyNoNet" ? "ready is-nonet" : key}`;
    txt.textContent = s.text;
    el.title = s.tip + extra;
    el.setAttribute("aria-label", `Server status: ${s.tip}${extra}`);
    el.dataset.state = key;
  }

  async function ping() {
    if (inflight) return inflight;
    inflight = (async () => {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), TIMEOUT_MS);
      try {
        const r = await fetch("/api/health", { cache: "no-store", signal: ctl.signal, headers: { Accept: "application/json" } });
        if (!r.ok) throw new Error(String(r.status));
        const h = await r.json();
        if (h.offline_ready) show(navigator.onLine ? "ready" : "readyNoNet");
        else {
          const missing = Object.entries(h.components || {}).filter(([, ok]) => !ok).map(([k]) => k.replace(/_/g, " "));
          show(h.ready === false ? "checking" : "degraded", missing.join(", ") || "starting up");
        }
      } catch {
        show("down");
      } finally {
        clearTimeout(t);
        inflight = null;
      }
    })();
    return inflight;
  }

  const schedule = () => { clearTimeout(timer); timer = setTimeout(async () => { await ping(); schedule(); }, PING_MS); };
  addEventListener("online", ping);
  addEventListener("offline", ping);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") ping(); });
  show("checking");
  ping().then(schedule);
}
