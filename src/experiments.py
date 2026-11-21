"""Reproduce the "can anything beat the deployed model?" experiments.

Run:  python -m src.experiments            (3-week horizon, ~2 min)
      python -m src.experiments --h 2

Every candidate uses the same chronological protocol as src/train.py:
fit on 2017-2022 -> score on 2023 (validation) -> refit on 2017-2023 -> score once on 2024-2025 (test).
Optional extras: optuna (tuning), xgboost (ensembles). Install with requirements-dev.txt.
"""
from __future__ import annotations

import argparse
import warnings

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import PoissonRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.data import load_raw
from src.features import build_base_features, feature_columns, make_supervised
from src.tiers import assign_tier, fit_thresholds
from src.train import fit_lgb, metrics, split

warnings.filterwarnings("ignore")


def tier_acc(y, p, th) -> float:
    return float((assign_tier(y, th) == assign_tier(np.clip(p, 0, None), th)).mean())


def poisson_floor(mu: np.ndarray) -> float:
    """Expected MAE of a PERFECT model if weekly counts are Poisson around the true mean."""
    k = np.arange(0, 80)[:, None]
    return float((poisson.pmf(k, mu[None, :]) * np.abs(k - mu[None, :])).sum(0).mean())


def mlp():
    return TransformedTargetRegressor(
        regressor=make_pipeline(StandardScaler(), MLPRegressor(hidden_layer_sizes=(128, 64), alpha=1e-3,
                                                               early_stopping=True, max_iter=300,
                                                               random_state=42)),
        func=np.log1p, inverse_func=np.expm1)


def main(h: int) -> None:
    raw = load_raw()
    sup = make_supervised(raw, h, base=build_base_features(raw))
    cols = feature_columns(sup, weather_only=True)
    tr, va, te = split(sup)
    trva = pd.concat([tr, va])
    th = fit_thresholds(tr["target"])
    n = fit_lgb(tr[cols], tr["target"], va[cols], va["target"]).best_iteration_
    n_final = int(n * 1.1)

    # Each candidate: (fit on train -> val preds, fit on train+val -> test preds)
    fitters = {
        "LightGBM (deployed)": lambda X, y, nt: fit_lgb(X, y, n_estimators=nt),
        "Random forest": lambda X, y, nt: RandomForestRegressor(200, min_samples_leaf=5, n_jobs=-1,
                                                                random_state=42).fit(X, y),
        "Poisson regression": lambda X, y, nt: make_pipeline(StandardScaler(),
                                                             PoissonRegressor(alpha=1e-3, max_iter=1000)).fit(X, y),
        "Neural net (MLP)": lambda X, y, nt: mlp().fit(X, y),
    }
    try:
        from xgboost import XGBRegressor
        fitters["XGBoost"] = lambda X, y, nt: XGBRegressor(objective="count:poisson", n_estimators=600,
                                                           learning_rate=0.03, max_depth=6, subsample=0.8,
                                                           colsample_bytree=0.8, random_state=42,
                                                           n_jobs=-1).fit(X, y)
    except ImportError:
        pass

    pv, pt = {}, {}
    for name, fit in fitters.items():
        pv[name] = fit(tr[cols], tr["target"], n).predict(va[cols])
        pt[name] = fit(trva[cols], trva["target"], n_final).predict(te[cols])

    if "XGBoost" in pv:
        for name, keys in {"Ensemble: LightGBM + XGBoost": ["LightGBM (deployed)", "XGBoost"],
                           "Ensemble: all models, equal": list(fitters)}.items():
            pv[name] = np.mean([pv[k] for k in keys], axis=0)
            pt[name] = np.mean([pt[k] for k in keys], axis=0)

    tw = dict(objective="tweedie", tweedie_variance_power=1.3)
    pv["LightGBM, Tweedie loss"] = fit_lgb(tr[cols], tr["target"], n_estimators=n, **tw).predict(va[cols])
    pt["LightGBM, Tweedie loss"] = fit_lgb(trva[cols], trva["target"], n_estimators=n_final, **tw).predict(te[cols])

    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        def objective(trial):
            p = dict(learning_rate=trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
                     num_leaves=trial.suggest_int("num_leaves", 7, 63),
                     min_child_samples=trial.suggest_int("min_child_samples", 10, 200, log=True),
                     colsample_bytree=trial.suggest_float("colsample_bytree", 0.4, 1.0),
                     reg_lambda=trial.suggest_float("reg_lambda", 1e-3, 20, log=True),
                     subsample=trial.suggest_float("subsample", 0.5, 1.0))
            m = fit_lgb(tr[cols], tr["target"], va[cols], va["target"], **p)
            trial.set_user_attr("n", m.best_iteration_)
            trial.set_user_attr("pred", m.predict(va[cols]))
            return float(np.abs(va["target"] - m.predict(va[cols])).mean())

        study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=30)
        best = study.best_trial
        pv["LightGBM, Optuna-tuned"] = best.user_attrs["pred"]
        pt["LightGBM, Optuna-tuned"] = fit_lgb(trva[cols], trva["target"], n_estimators=int(best.user_attrs["n"] * 1.1),
                                               **study.best_params).predict(te[cols])
    except ImportError:
        print("(optuna not installed: skipping tuning)")

    rows = []
    for name in pv:
        v, t = metrics(va["target"], pv[name], th), metrics(te["target"], pt[name], th)
        rows.append({"approach": name, "val_MAE": v["MAE"], "test_MAE": t["MAE"], "test_RMSE": t["RMSE"],
                     "test_tier_accuracy": round(tier_acc(te["target"], pt[name], th), 3),
                     "test_high_risk_recall": t["high_risk_recall"]})
    out = pd.DataFrame(rows).sort_values("test_MAE")
    print(f"\nHorizon {h} weeks — validation 2023, test 2024-2025\n")
    print(out.to_string(index=False))
    deployed = np.clip(pt["LightGBM (deployed)"], 1e-6, None)
    print(f"\nNoise floor: a perfect model would still average ~{poisson_floor(deployed):.2f} cases off "
          f"(deployed: {out.set_index('approach').loc['LightGBM (deployed)', 'test_MAE']:.2f}).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=3, choices=[2, 3, 4])
    main(ap.parse_args().h)
