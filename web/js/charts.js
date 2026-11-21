// ECharts wrappers. One theme object built from CSS variables, rebuilt whenever the theme toggles.
import { day, esc, fmt, month, reducedMotion } from "./ui.js";

const registry = new Set();  // live charts, re-rendered on theme change

export function tokens() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n) => cs.getPropertyValue(n).trim();
  return {
    dark: document.documentElement.dataset.theme === "dark",
    ink: v("--ink"), ink2: v("--ink-2"), ink3: v("--ink-3"), line: v("--line"), line2: v("--line-2"),
    surface: v("--surface"), surface2: v("--surface-2"), surface3: v("--surface-3"), accent: v("--accent"),
    tipBg: v("--tip-bg"), font: v("--font"), mono: v("--mono"),
    tier: { Low: v("--t-Low"), Medium: v("--t-Medium"), High: v("--t-High"), Critical: v("--t-Critical") },
  };
}

/** Mix two hex colours (t = share of b). */
export function mix(a, b, t) {
  const p = (h) => { h = h.replace("#", ""); if (h.length === 3) h = [...h].map((c) => c + c).join(""); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
  const [x, y] = [p(a), p(b)];
  return `#${x.map((c, i) => Math.round(c + (y[i] - c) * t).toString(16).padStart(2, "0")).join("")}`;
}
export const alpha = (hex, a) => { const h = mix(hex, hex, 0).slice(1); return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a})`; };

function base(t) {
  const anim = !reducedMotion();
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: t.font, color: t.ink2 },
    animation: anim, animationDuration: 700, animationEasing: "cubicOut",
    animationDurationUpdate: 400, animationEasingUpdate: "cubicOut",
    tooltip: {
      backgroundColor: t.tipBg, borderColor: t.line2, borderWidth: 1, padding: [10, 12],
      textStyle: { color: t.ink, fontFamily: t.font, fontSize: 12.5 },
      extraCssText: `backdrop-filter: blur(12px) saturate(1.4); -webkit-backdrop-filter: blur(12px) saturate(1.4); border-radius: 12px; box-shadow: 0 16px 40px -16px rgba(0,0,0,${t.dark ? 0.8 : 0.25});`,
      transitionDuration: 0.15, confine: true,
    },
  };
}
const axisLabel = (t, extra = {}) => ({ color: t.ink3, fontFamily: t.mono, fontSize: 11, ...extra });
const splitLine = (t) => ({ show: true, lineStyle: { color: t.line, width: 1 } });

const row = (label, value, color = "") =>
  `<div class="ec-tip__r"><span>${color ? `<i style="background:${color}"></i>` : ""}${esc(label)}</span><b>${esc(value)}</b></div>`;

/** Create a chart bound to an element; `render(builder)` keeps the builder so theme changes redraw it. */
export function makeChart(el, { renderer = "svg" } = {}) {
  const inst = window.echarts.init(el, null, { renderer });
  const ro = new ResizeObserver(() => inst.resize());
  ro.observe(el);
  const c = {
    inst, builder: null,
    render(builder, { replace = false } = {}) {
      c.builder = builder;
      inst.setOption(builder(tokens()), { notMerge: replace, lazyUpdate: false });
    },
    retheme() { if (c.builder) inst.setOption(c.builder(tokens()), { notMerge: true }); },
    dispose() { ro.disconnect(); registry.delete(c); inst.dispose(); },
  };
  registry.add(c);
  return c;
}
export function rethemeAll() { for (const c of registry) c.retheme(); }
export function disposeAll() { for (const c of [...registry]) c.dispose(); }

// ------------------------------------------------------------------ season: forecast line + likely range + recorded
export function seasonOption(season, targetDate, tier) {
  return (t) => {
    const xs = season.map((d) => d.date);
    const lo = season.map((d) => [d.date, d.low]);
    const band = season.map((d) => [d.date, Math.max(0, d.high - d.low)]);
    const exp = season.map((d) => [d.date, d.expected]);
    const act = season.filter((d) => d.actual !== null).map((d) => [d.date, d.actual]);
    const tgt = season.find((d) => d.date === targetDate);
    const byDate = Object.fromEntries(season.map((d) => [d.date, d]));
    return {
      ...base(t),
      grid: { left: 8, right: 16, top: 28, bottom: 4, containLabel: true },
      xAxis: {
        type: "time", min: xs[0], max: xs[xs.length - 1], boundaryGap: false,
        axisLine: { show: true, lineStyle: { color: t.line2 } }, axisTick: { show: false }, splitLine: { show: false },
        axisLabel: axisLabel(t, { formatter: (v) => month(new Date(v).toISOString().slice(0, 10)), hideOverlap: true, margin: 12 }),
        axisPointer: { show: true, type: "line", lineStyle: { color: t.line2, width: 1 }, label: { show: false } },
      },
      yAxis: {
        type: "value", min: 0, minInterval: 1, splitNumber: 4,
        axisLine: { show: false }, axisTick: { show: false }, splitLine: splitLine(t), axisLabel: axisLabel(t, { margin: 10 }),
      },
      tooltip: {
        ...base(t).tooltip, trigger: "axis",
        formatter: (ps) => {
          const d = byDate[new Date(ps[0].value[0]).toISOString().slice(0, 10)];
          if (!d) return "";
          return `<div class="ec-tip"><div class="ec-tip__t">Week of ${day(d.date, true)}</div>` +
            row("Forecast", `${fmt(d.expected)} cases`, t.accent) +
            row("Likely range", `${Math.round(d.low)}–${Math.round(d.high)}`, alpha(t.accent, 0.3)) +
            row("Recorded", d.actual === null ? "Not yet recorded" : `${d.actual} cases`, t.ink) + `</div>`;
        },
      },
      series: [
        { name: "low", type: "line", data: lo, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, silent: true, smooth: 0.35, animation: false },
        {
          name: "Likely range", type: "line", data: band, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, smooth: 0.35, silent: true,
          areaStyle: { color: new window.echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: alpha(t.accent, t.dark ? 0.32 : 0.22) }, { offset: 1, color: alpha(t.accent, t.dark ? 0.1 : 0.06) }]) },
        },
        {
          name: "Forecast", type: "line", data: exp, smooth: 0.35, symbol: "circle", symbolSize: 6, showSymbol: false,
          lineStyle: { color: t.accent, width: 2.4, cap: "round" }, itemStyle: { color: t.accent, borderColor: t.surface, borderWidth: 2 },
          emphasis: { focus: "none", scale: 1.6 }, z: 3,
          markLine: tgt ? {
            silent: true, symbol: "none", animation: false,
            lineStyle: { color: t.ink3, type: [3, 4], width: 1 },
            label: { formatter: "This forecast", position: "end", color: t.ink2, fontFamily: t.font, fontSize: 11.5, fontWeight: 500, distance: 6 },
            data: [{ xAxis: targetDate }],
          } : undefined,
        },
        {
          name: "Recorded", type: "scatter", data: act, symbolSize: 6.5, z: 4,
          itemStyle: { color: t.ink, borderColor: t.surface, borderWidth: 1.5 },
        },
        tgt ? {
          name: "This week", type: "scatter", data: [[tgt.date, tgt.expected]], symbolSize: 13, z: 5, silent: true,
          itemStyle: { color: t.tier[tier], borderColor: t.surface, borderWidth: 3, shadowBlur: 12, shadowColor: alpha(t.tier[tier], 0.55) },
        } : null,
      ].filter(Boolean),
    };
  };
}

// ------------------------------------------------------------------ city heatmap: ward x week tiers
const heatWidth = new WeakMap();
export function heatmapOption(hm, markWeek, width = 900) {
  heatWidth.set(hm, width);
  return (t) => {
    const weeks = hm.weeks;
    const wards = hm.wards.map((w) => w.label);
    const colors = [
      mix(t.surface, t.tier.Low, t.dark ? 0.15 : 0.18),
      t.tier.Medium,
      t.tier.High, t.tier.Critical,
    ];
    // narrow charts: label every third month so ticks never collide
    const compact = (heatWidth.get(hm) || 900) < 560;
    let lastM = "", seen = 0;
    const monthTick = weeks.map((w) => {
      const m = month(w); if (m === lastM) return ""; lastM = m;
      return compact && seen++ % 3 ? "" : m;
    });
    const hasMark = weeks.includes(markWeek);
    return {
      ...base(t),
      grid: { left: 4, right: 8, top: hasMark ? 22 : 6, bottom: 4, containLabel: true },
      xAxis: {
        type: "category", data: weeks, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false },
        axisLabel: axisLabel(t, { interval: 0, formatter: (_v, i) => monthTick[i], margin: 10 }),
      },
      yAxis: {
        type: "category", data: wards, inverse: true, axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: t.ink2, fontFamily: t.font, fontSize: 12, margin: 12 },
      },
      visualMap: { show: false, type: "piecewise", dimension: 2, pieces: colors.map((c, i) => ({ value: i, color: c })) },
      tooltip: {
        ...base(t).tooltip, trigger: "item",
        formatter: (p) => {
          const [x, y, ti, val, actual] = p.value;
          return `<div class="ec-tip"><div class="ec-tip__t">${esc(wards[y])} · week of ${day(weeks[x], true)}</div>` +
            row("Risk", hm.tiers[ti], t.tier[hm.tiers[ti]]) + row("Expected", `${fmt(val)} cases`) +
            row("Recorded", actual === null || actual === undefined ? "Not yet recorded" : `${actual} cases`) + `</div>`;
        },
      },
      series: [{
        type: "heatmap", data: hm.cells, progressive: 0,
        itemStyle: { borderColor: t.surface, borderWidth: 2, borderRadius: 3 },
        emphasis: { itemStyle: { borderColor: t.ink, borderWidth: 1.5 } },
        markLine: hasMark ? {
          silent: true, symbol: "none", animation: false, lineStyle: { color: t.ink, width: 1.5, type: "solid" },
          label: { formatter: "Selected week", position: "start", color: t.ink, fontFamily: t.font, fontSize: 11, fontWeight: 600, distance: 4 },
          data: [{ xAxis: markWeek }],
        } : undefined,
      }],
    };
  };
}

// ------------------------------------------------------------------ model: error by model (horizontal bars)
export function modelBarsOption(comparison) {
  return (t) => {
    const d = [...comparison].sort((a, b) => a.MAE - b.MAE);
    const max = Math.max(...d.map((x) => x.MAE));
    return {
      ...base(t),
      grid: { left: 4, right: 48, top: 4, bottom: 4, containLabel: true },
      xAxis: { type: "value", max: max * 1.08, show: false },
      yAxis: {
        type: "category", data: d.map((x) => x.label), inverse: true, axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { fontFamily: t.font, fontSize: 12.5, margin: 14,
          formatter: (v) => (v.includes("ours") ? `{o|${v}}` : v), color: t.ink2,
          rich: { o: { color: t.ink, fontWeight: 600, fontFamily: t.font, fontSize: 12.5 } } },
      },
      tooltip: { ...base(t).tooltip, trigger: "item",
        formatter: (p) => { const x = d[p.dataIndex]; return `<div class="ec-tip"><div class="ec-tip__t">${esc(x.label)}</div>` +
          row("Average error", `${x.MAE.toFixed(2)} cases`) + row("RMSE", x.RMSE.toFixed(2)) +
          row("High-risk weeks caught", `${Math.round(x.high_risk_recall * 100)}%`) + `</div>`; } },
      series: [{
        type: "bar", barWidth: 18, data: d.map((x) => ({
          value: x.MAE,
          itemStyle: { color: x.ours ? new window.echarts.graphic.LinearGradient(0, 0, 1, 0, [{ offset: 0, color: alpha(t.accent, 0.75) }, { offset: 1, color: t.accent }]) : mix(t.surface3, t.ink3, t.dark ? 0.32 : 0.12), borderRadius: [0, 6, 6, 0] },
          label: { show: true, position: "right", distance: 8, formatter: x.MAE.toFixed(2), color: x.ours ? t.accent : t.ink2, fontFamily: t.mono, fontSize: 12, fontWeight: x.ours ? 600 : 400 },
        })),
        showBackground: true, backgroundStyle: { color: "transparent" },
      }],
    };
  };
}

// ------------------------------------------------------------------ model: error by year, all horizons
export function backtestOption(backtest, selected) {
  return (t) => {
    const hs = Object.keys(backtest).map(Number).sort((a, b) => (a === selected) - (b === selected));
    const years = backtest[String(selected)].map((r) => r.year);
    return {
      ...base(t),
      grid: { left: 4, right: 52, top: 16, bottom: 4, containLabel: true },
      xAxis: { type: "category", data: years.map(String), boundaryGap: false, axisLine: { show: true, lineStyle: { color: t.line2 } },
               axisTick: { show: false }, axisLabel: axisLabel(t, { margin: 12 }),
               axisPointer: { show: true, type: "line", lineStyle: { color: t.line2 }, label: { show: false } } },
      yAxis: { type: "value", min: 0, splitNumber: 3, axisLine: { show: false }, axisTick: { show: false }, splitLine: splitLine(t),
               axisLabel: axisLabel(t, { formatter: (v) => v.toFixed(1) }) },
      tooltip: { ...base(t).tooltip, trigger: "axis",
        formatter: (ps) => `<div class="ec-tip"><div class="ec-tip__t">${ps[0].axisValue}</div>` +
          [...ps].sort((a, b) => Number(a.seriesName) - Number(b.seriesName)).map((p) =>
            row(`${p.seriesName} weeks ahead`, `${Number(p.value).toFixed(2)}`, Number(p.seriesName) === selected ? t.accent : t.ink3)).join("") + `</div>` },
      series: hs.map((h) => {
        const on = h === selected;
        const col = on ? t.accent : alpha(t.ink3, t.dark ? 0.55 : 0.5);
        return {
          name: String(h), type: "line", data: backtest[String(h)].map((r) => r.MAE), smooth: 0.25, z: on ? 3 : 2,
          symbol: "circle", symbolSize: on ? 7 : 5, showSymbol: true,
          lineStyle: { color: col, width: on ? 2.5 : 1.5 }, itemStyle: { color: col, borderColor: t.surface, borderWidth: on ? 2 : 1.5 },
          areaStyle: on ? { color: new window.echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: alpha(t.accent, t.dark ? 0.2 : 0.12) }, { offset: 1, color: alpha(t.accent, 0) }]) } : undefined,
          endLabel: { show: true, formatter: `${h} wk`, color: on ? t.accent : t.ink3, fontFamily: t.mono, fontSize: 11.5, fontWeight: on ? 600 : 400, distance: 8 },
          labelLayout: { moveOverlap: "shiftY" },
          emphasis: { focus: "series" },
        };
      }),
    };
  };
}
