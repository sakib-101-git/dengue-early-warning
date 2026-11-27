"""What-if vector control: model-driven counterfactual of larviciding / fogging crews.

1. Mechanism. A crew removes breeding sites (larvicide, clearing stagnant water), modelled as a cut
   in the ward's water_logging_risk_index:
       reduction(crews) = min(CAP, 1 - (1 - e)^crews)        e = 10% per crew by default (5-20%)
   The index feeds two model features: the index itself and rain_sum_4w x index. Both are linear in
   the index, so scaling them by (1 - reduction) is exactly what a full feature rebuild from the
   modified raw rows gives (RiskEngine.what_if_frame(waterlog_pct=...); tests check they match).
2. Counterfactual. Crews are deployed in the issue week. The deployed LightGBM models (point, q10,
   q90) are re-run for h = 2, 3 and 4 weeks ahead, so the effect lands in the 2-to-4-week window.
3. Monotone guard. Tree models are step functions, so a slightly lower index can in principle give
   a slightly higher prediction. The scenario for c crews is therefore the lowest prediction over
   0..c crews (a team can always do less): more crews never increase cases, 0 crews = baseline.
4. Impact per ward and horizon: baseline vs scenario cases, beds and saline (round(k x cases) with
   the learned supply rates), prevented = baseline - scenario. ROI = cases prevented over
   h2 + h3 + h4 / crews.
5. Ranking: wards by 1-crew ROI. Auto-deploy N crews: greedy by marginal ROI. Each step gives the
   next crew(s) to the ward with the most extra cases prevented per crew; it looks ahead past
   plateaus (where one more crew alone changes nothing in a tree model) by comparing every
   increment's average gain per crew.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.config import HORIZONS
from src.resources import rate_k, units
from src.tiers import assign_tier

EFFECT_DEFAULT = 10            # % cut in the water-logging index per crew
EFFECT_RANGE = (5, 20)         # adjustable, %
CAP = 0.50                     # the index is never cut by more than 50%
MAX_CREWS_PER_WARD = 20
WLI_COLS = ("water_logging_risk_index", "rain4w_x_waterlogging")   # the features that scale with the index
SIM_SUPPLIES = ("beds", "saline")
TIER_ORDER = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}

METHOD = (
    "Each crew cuts the ward's water-logging risk index by the effect per crew, compounding: "
    "reduction = 1 − (1 − effect)^crews, capped at 50%. The index enters the model directly and "
    "through rain × water-logging, so the deployed LightGBM models are re-run on the changed "
    "features for 2, 3 and 4 weeks ahead. More crews never raise a forecast (lowest prediction "
    "over 0..n crews). ROI = cases prevented over the three forecast weeks ÷ crews."
)


def reduction(crews, effect_pct: float = EFFECT_DEFAULT, cap: float = CAP) -> np.ndarray:
    """Share of the water-logging index removed by `crews` crews (array-friendly)."""
    c = np.clip(np.asarray(crews, dtype=float), 0, None)
    return np.minimum(cap, 1 - (1 - effect_pct / 100) ** c)


def useful_crews(effect_pct: float = EFFECT_DEFAULT, cap: float = CAP) -> int:
    """Crews at which the cap is reached; more crews in one ward add nothing."""
    return int(math.ceil(math.log(1 - cap) / math.log(1 - effect_pct / 100) - 1e-9))


def apply_reduction(X: pd.DataFrame, r) -> pd.DataFrame:
    """Scale the water-logging features by (1 - r); r is a scalar or one value per row."""
    out = X.copy()
    keep = 1 - np.asarray(r, dtype=float)
    for c in WLI_COLS:
        if c in out.columns:
            out[c] = out[c].to_numpy(float) * keep
    return out


# ---------------------------------------------------------------- response curves (the cached core)
def response_curves(engine, issue, effect_pct: float = EFFECT_DEFAULT) -> pd.DataFrame:
    """Predictions for every ward x horizon x crew level 0..useful_crews at one issue week.

    One batched predict per model and horizon (60 wards x ~8-15 levels), a few ms in total.
    Columns: ward_id, city, h, target_date, crews, reduction, expected, low_80, high_80, tier,
    model_expected (raw, before the monotone guard).
    """
    levels = np.arange(useful_crews(effect_pct) + 1)
    red = reduction(levels, effect_pct)
    parts = []
    for h in HORIZONS:
        b = engine.bundles[h]
        rows = engine.feature_rows(issue, h)
        n = len(rows)
        X = rows[b["features"]].loc[rows.index.repeat(len(levels))].reset_index(drop=True)
        r = np.tile(red, n)
        Xs = apply_reduction(X, r)
        mu = np.clip(b["model"].predict(Xs), 0, None)
        lo = np.clip(b["q10"].predict(Xs), 0, None)
        hi = b["q90"].predict(Xs)
        d = pd.DataFrame({
            "ward_id": np.repeat(rows["ward_id"].to_numpy(), len(levels)),
            "city": np.repeat(rows["city"].to_numpy(), len(levels)),
            "h": h, "target_date": np.repeat(rows["target_date"].to_numpy(), len(levels)),
            "crews": np.tile(levels, n), "reduction": r, "model_expected": mu,
        })
        # monotone guard: c crews can always do what fewer crews did
        d["expected"] = pd.Series(mu).groupby(d["ward_id"], sort=False).cummin().to_numpy()
        d["low_80"] = pd.Series(lo).groupby(d["ward_id"], sort=False).cummin().to_numpy()
        d["high_80"] = np.maximum(pd.Series(hi).groupby(d["ward_id"], sort=False).cummin().to_numpy(), d["expected"])
        d["low_80"] = np.minimum(d["low_80"], d["expected"])
        d["tier"] = assign_tier(d["expected"], b["thresholds"])
        parts.append(d)
    return pd.concat(parts, ignore_index=True)


class Curves:
    """Fast lookups over response_curves(): arrays indexed [ward, horizon, crews]."""

    def __init__(self, curves: pd.DataFrame, effect_pct: float):
        self.df = curves
        self.effect_pct = effect_pct
        self.max_crews = int(curves["crews"].max())
        self.wards = sorted(curves["ward_id"].unique())
        self.idx = {w: i for i, w in enumerate(self.wards)}
        self.city = curves.drop_duplicates("ward_id").set_index("ward_id")["city"].to_dict()
        self.horizons = sorted(curves["h"].unique())
        self.target_dates = {int(h): pd.Timestamp(curves.loc[curves.h == h, "target_date"].iloc[0]) for h in self.horizons}
        shape = (len(self.wards), len(self.horizons), self.max_crews + 1)
        d = curves.sort_values(["ward_id", "h", "crews"])
        self.cube = {c: d[c].to_numpy(float).reshape(shape) for c in ("expected", "low_80", "high_80", "reduction")}
        self.tier = d["tier"].to_numpy(object).reshape(shape)
        exp = self.cube["expected"]
        # cases prevented over h2+h3+h4, per ward and crew level (non-decreasing by construction)
        self.prevented = (exp[:, :, :1] - exp).sum(axis=1)

    def level(self, crews: int) -> int:
        return int(min(max(crews, 0), self.max_crews))

    def peak_tier(self, i: int, c: int = 0) -> str:
        return max(self.tier[i, :, self.level(c)], key=TIER_ORDER.get)


# ---------------------------------------------------------------- scenario impact
def _point(cv: Curves, i: int, j: int, c: int, k: dict) -> dict:
    c = cv.level(c)
    cases = float(cv.cube["expected"][i, j, c])
    out = {"cases": cases, "low_80": float(cv.cube["low_80"][i, j, c]),
           "high_80": float(cv.cube["high_80"][i, j, c]), "tier": str(cv.tier[i, j, c])}
    for s in SIM_SUPPLIES:
        out[s] = int(units(k[s], [cases])[0])
    return out


def simulate(cv: Curves, crews: dict[str, int], rates: dict, scope_wards: list[str] | None = None) -> dict:
    """Impact of a crew plan {ward_id: crews}. Wards with 0 crews are ignored."""
    k = rate_k(rates)
    plan = {w: int(n) for w, n in crews.items() if int(n) > 0}
    wards = []
    for w, n in plan.items():
        i = cv.idx[w]
        hs = []
        for j, h in enumerate(cv.horizons):
            base, scen = _point(cv, i, j, 0, k), _point(cv, i, j, n, k)
            hs.append({"h": int(h), "target_date": cv.target_dates[int(h)], "baseline": base, "scenario": scen,
                       "prevented": {"cases": base["cases"] - scen["cases"],
                                     **{s: base[s] - scen[s] for s in SIM_SUPPLIES}}})
        prev = {key: sum(x["prevented"][key] for x in hs) for key in ("cases", *SIM_SUPPLIES)}
        wards.append({"ward_id": w, "city": cv.city[w], "crews": n, "effective_crews": cv.level(n),
                      "reduction": float(cv.cube["reduction"][i, 0, cv.level(n)]),
                      "tier": cv.peak_tier(i), "tier_after": cv.peak_tier(i, n),
                      "horizons": hs, "prevented": prev, "roi": prev["cases"] / n})
    wards.sort(key=lambda x: (-x["prevented"]["cases"], x["ward_id"]))

    by_h = []
    for j, h in enumerate(cv.horizons):
        row = {"h": int(h), "target_date": cv.target_dates[int(h)]}
        for key in ("cases", *SIM_SUPPLIES):
            row[f"baseline_{key}"] = sum(x["horizons"][j]["baseline"][key] for x in wards)
            row[f"scenario_{key}"] = sum(x["horizons"][j]["scenario"][key] for x in wards)
            row[f"prevented_{key}"] = row[f"baseline_{key}"] - row[f"scenario_{key}"]
        by_h.append(row)
    total_crews = sum(plan.values())
    prevented = {key: sum(r[f"prevented_{key}"] for r in by_h) for key in ("cases", *SIM_SUPPLIES)}
    scope = scope_wards if scope_wards is not None else cv.wards
    scope_cases = float(sum(cv.cube["expected"][cv.idx[w], :, 0].sum() for w in scope))
    return {
        "wards": wards, "by_horizon": by_h, "prevented": prevented,
        "crews_deployed": total_crews, "wards_deployed": len(wards),
        "roi": prevented["cases"] / total_crews if total_crews else 0.0,
        "scope_baseline_cases": scope_cases,
        "share_of_scope": prevented["cases"] / scope_cases if scope_cases else 0.0,
    }


# ---------------------------------------------------------------- ranking + auto-deploy
def rank(cv: Curves, wards: list[str]) -> pd.DataFrame:
    """Every ward in scope by 1-crew ROI (cases prevented over h2-h4 by one crew)."""
    rows = []
    for w in wards:
        i = cv.idx[w]
        g = cv.prevented[i]
        per_crew = g[1:] / np.arange(1, len(g))
        best = int(np.argmax(per_crew)) + 1
        exp0 = cv.cube["expected"][i, :, 0]
        rows.append({"ward_id": w, "city": cv.city[w], "tier": cv.peak_tier(i),
                     "baseline_cases": float(exp0.sum()), **{f"baseline_h{h}": float(v) for h, v in zip(cv.horizons, exp0)},
                     "roi_1": float(g[1]), "best_roi": float(per_crew[best - 1]), "best_roi_crews": best,
                     "prevented_max": float(g[-1]), "crews_max": cv.max_crews})
    d = pd.DataFrame(rows)
    d = d.sort_values(["roi_1", "best_roi", "baseline_cases", "ward_id"], ascending=[False, False, False, True], kind="mergesort")
    d["rank"] = np.arange(1, len(d) + 1)
    return d.reset_index(drop=True)


def auto_deploy(cv: Curves, n: int, wards: list[str], start: dict[str, int] | None = None) -> dict[str, int]:
    """Greedy marginal ROI: repeatedly give crews to the ward/increment with the best gain per crew.

    Stops when crews run out or no ward can prevent any more cases. Deterministic: ties go to the
    ward listed first (ward id order), then to the smaller increment.
    """
    ws = sorted(wards)
    rows = np.array([cv.idx[w] for w in ws])
    G = cv.prevented[rows]                                   # (wards, levels)
    L = cv.max_crews
    alloc = np.array([min((start or {}).get(w, 0), L) for w in ws], dtype=int)
    left = int(max(n, 0))
    steps = np.arange(1, L + 1)
    while left > 0:
        target = alloc[:, None] + steps[None, :]              # c + j
        ok = (target <= L) & (steps[None, :] <= left)
        gain = np.where(ok, G[np.arange(len(ws))[:, None], np.minimum(target, L)] - G[np.arange(len(ws)), alloc][:, None], -np.inf)
        ratio = gain / steps[None, :]
        flat = int(np.argmax(ratio))                          # row-major: first ward, then smallest j
        i, j = divmod(flat, L)
        if not np.isfinite(ratio[i, j]) or ratio[i, j] <= 1e-9:
            break
        alloc[i] += j + 1
        left -= j + 1
    return {w: int(c) for w, c in zip(ws, alloc) if c > 0}
