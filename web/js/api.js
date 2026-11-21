// Thin fetch wrapper: one in-flight request per "channel"; a newer request aborts the stale one.
const inflight = new Map();
const cache = new Map();   // small response cache so toggling back and forth is instant
const CACHE_MAX = 120;

export class ApiError extends Error {}

export async function api(path, params = {}, channel = path) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const url = `/api/${path}${q.toString() ? `?${q}` : ""}`;
  if (cache.has(url)) return cache.get(url);

  inflight.get(channel)?.abort();
  const ctl = new AbortController();
  inflight.set(channel, ctl);
  let res;
  try {
    res = await fetch(url, { signal: ctl.signal, headers: { Accept: "application/json" } });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError("Can't reach the Dengue Watch server. Is it still running?");
  } finally {
    if (inflight.get(channel) === ctl) inflight.delete(channel);
  }
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new ApiError(body?.detail && typeof body.detail === "string" ? body.detail : `The server answered ${res.status}.`);
  cache.set(url, body);
  if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value);
  return body;
}

export const isAbort = (e) => e?.name === "AbortError";
