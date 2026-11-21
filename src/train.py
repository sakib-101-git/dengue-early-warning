"""Train, compare and save the dengue risk models.

Run:  python -m src.train          (about 1-2 minutes on a laptop CPU)

Steps per horizon h in {2, 3, 4} weeks:
  1. Build lag features and the label "cases at week t+h".
  2. Chronological split by target year: train 2017-2022 | validation 2023 | test 2024-2025.
  3. Compare baselines + statistical + ML models on validation.
  4. Refit the deployed LightGBM (Poisson) on train+validation, plus 10% / 90% quantile models.
  5. Report held-out test metrics once, a weather-only ablation, and a year-by-year backtest.
  6. Serialise everything with joblib -> models/model_h{h}.joblib, models/metrics.json.
"""
from __future__ import annotations

import json
import time
import warnings
from datetime import datetime, timezone

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import PoissonRegressor
from sklearn.metrics import f1_score, mean_absolute_error, mean_squared_error, recall_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.config import (
    HORIZONS, METRICS_JSON, MODELS_DIR, RANDOM_STATE, TEST_YEARS, TRAIN_END_YEAR,
    USE_CASE_HISTORY, VAL_YEAR,
)
from src.data import load_raw
from src.features import build_base_features, feature_columns, make_supervised
from src.tiers import assign_tier, fit_thresholds

try:  # optional: only used in the comparison table
    from xgboost import XGBRegressor
except ImportError:  # pragma: no cover
    XGBRegressor = None

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

LGB_PARAMS = dict(
    objective="poisson", learning_rate=0.03, num_leaves=31, min_child_samples=40,
    subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
    n_estimators=3000, random_state=RANDOM_STATE, verbose=-1, n_jobs=-1,
)


def metrics(y_true, y_pred, thresholds) -> dict:
    y_pred = np.clip(np.asarray(y_pred, float), 0, None)
    t_true, t_pred = assign_tier(y_true, thresholds), assign_tier(y_pred, thresholds)
    hi = lambda t: np.isin(t, ["High", "Critical"])  # noqa: E731
    return {
        "MAE": round(float(mean_absolute_error(y_true, y_pred)), 3),
        "RMSE": round(float(np.sqrt(mean_squared_error(y_true, y_pred))), 3),
        "tier_macro_F1": round(float(f1_score(t_true, t_pred, average="macro")), 3),
        "high_risk_recall": round(float(recall_score(hi(t_true), hi(t_pred), zero_division=0)), 3),
    }


def split(sup: pd.DataFrame):
    sup = sup.dropna(subset=["target"])
    y = sup["target_date"].dt.year
    return sup[y <= TRAIN_END_YEAR], sup[y == VAL_YEAR], sup[y.isin(TEST_YEARS)]


def fit_lgb(X, y, X_val=None, y_val=None, n_estimators=None, **overrides):
    params = {**LGB_PARAMS, **overrides}
    if n_estimators:
        params["n_estimators"] = n_estimators
    model = lgb.LGBMRegressor(**params)
    if X_val is not None:
        model.fit(X, y, eval_set=[(X_val, y_val)],
                  callbacks=[lgb.early_stopping(100, verbose=False)])
    else:
        model.fit(X, y)
    return model


def compare_models(tr, va, cols, thresholds) -> tuple[dict, int]:
    Xtr, ytr, Xva, yva = tr[cols], tr["target"], va[cols], va["target"]
    res = {
        "Baseline: persistence (cases now)": metrics(yva, va["cases_lag_0w"], thresholds),
        "Baseline: same week last year": metrics(yva, va["cases_same_week_last_year"], thresholds),
    }
    glm = make_pipeline(StandardScaler(), PoissonRegressor(alpha=1e-3, max_iter=1000)).fit(Xtr, ytr)
    res["Poisson GLM (statistical)"] = metrics(yva, glm.predict(Xva), thresholds)

    rf = RandomForestRegressor(n_estimators=200, min_samples_leaf=5, n_jobs=-1,
                               random_state=RANDOM_STATE).fit(Xtr, ytr)
    res["Random Forest"] = metrics(yva, rf.predict(Xva), thresholds)

    if XGBRegressor is not None:
        xgb = XGBRegressor(objective="count:poisson", n_estimators=600, learning_rate=0.03,
                           max_depth=6, subsample=0.8, colsample_bytree=0.8,
                           random_state=RANDOM_STATE, n_jobs=-1).fit(Xtr, ytr)
        res["XGBoost (Poisson)"] = metrics(yva, xgb.predict(Xva), thresholds)

    lgbm = fit_lgb(Xtr, ytr, Xva, yva)
    res["LightGBM (Poisson)  <- deployed"] = metrics(yva, lgbm.predict(Xva), thresholds)
    return res, int(lgbm.best_iteration_ or LGB_PARAMS["n_estimators"])


def backtest(sup, cols, n_estimators, thresholds, years=range(2019, 2026)) -> list[dict]:
    """Expanding-window chronological validation: train on all years < Y, test on Y."""
    sup = sup.dropna(subset=["target"])
    ty = sup["target_date"].dt.year
    rows = []
    for year in years:
        tr, te = sup[ty < year], sup[ty == year]
        m = fit_lgb(tr[cols], tr["target"], n_estimators=n_estimators)
        rows.append({"year": year, **metrics(te["target"], m.predict(te[cols]), thresholds)})
    return rows


def main() -> None:
    t0 = time.perf_counter()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    raw = load_raw()
    base = build_base_features(raw)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "split": {"train": f"2017-{TRAIN_END_YEAR}", "validation": str(VAL_YEAR),
                  "test": f"{TEST_YEARS[0]}-{TEST_YEARS[-1]}",
                  "rule": "split by year of the target week; never shuffled"},
        "horizons": {},
    }

    for h in HORIZONS:
        sup = make_supervised(raw, h, base=base)
        # Deployed feature set vs the alternative (ablation)
        cols = feature_columns(sup, weather_only=not USE_CASE_HISTORY)
        alt_cols = feature_columns(sup, weather_only=USE_CASE_HISTORY)
        alt_name = "test_without_case_history" if USE_CASE_HISTORY else "test_with_case_history"
        tr, va, te = split(sup)
        thresholds = fit_thresholds(tr["target"])
        print(f"\n=== Horizon {h} weeks | {len(cols)} features | "
              f"train {len(tr)} val {len(va)} test {len(te)} rows ===")

        val_table, best_iter = compare_models(tr, va, cols, thresholds)
        for name, m in val_table.items():
            print(f"  {name:38s} {m}")

        # Final models: refit on train+validation with the early-stopped tree count
        trva = pd.concat([tr, va])
        n_final = int(best_iter * 1.1)
        model = fit_lgb(trva[cols], trva["target"], n_estimators=n_final)
        q10 = fit_lgb(trva[cols], trva["target"], n_estimators=n_final, objective="quantile", alpha=0.1)
        q90 = fit_lgb(trva[cols], trva["target"], n_estimators=n_final, objective="quantile", alpha=0.9)

        pred = model.predict(te[cols])
        lo, hi = q10.predict(te[cols]), q90.predict(te[cols])
        coverage = float(np.mean((te["target"] >= lo) & (te["target"] <= hi)))

        alt_model = fit_lgb(trva[alt_cols], trva["target"], n_estimators=n_final)
        test_alt = metrics(te["target"], alt_model.predict(te[alt_cols]), thresholds)
        test_metrics = metrics(te["target"], pred, thresholds)
        print(f"  TEST (2024-25) deployed : {test_metrics} | 80% interval coverage {coverage:.2f}")
        print(f"  TEST {alt_name}: {test_alt}")

        bt = backtest(sup, cols, n_final, thresholds)
        imp = pd.Series(model.booster_.feature_importance("gain"), index=cols)
        imp = (imp / imp.sum()).sort_values(ascending=False).head(15).round(4)

        joblib.dump({"model": model, "q10": q10, "q90": q90, "features": cols,
                     "thresholds": thresholds, "horizon": h}, MODELS_DIR / f"model_h{h}.joblib",
                    compress=3)

        report["horizons"][str(h)] = {
            "tier_thresholds_cases": [round(t, 2) for t in thresholds],
            "validation_comparison": val_table,
            "deployed_model": "LightGBM (Poisson objective)",
            "n_trees": n_final,
            "test": test_metrics,
            alt_name: test_alt,
            "deployed_uses_case_history": USE_CASE_HISTORY,
            "test_interval_coverage_80": round(coverage, 3),
            "backtest_by_year": bt,
            "top_features_gain_share": imp.to_dict(),
        }

    report["train_seconds"] = round(time.perf_counter() - t0, 1)
    METRICS_JSON.write_text(json.dumps(report, indent=2))
    print(f"\nSaved models + {METRICS_JSON.name} in {report['train_seconds']} s")


if __name__ == "__main__":
    main()
