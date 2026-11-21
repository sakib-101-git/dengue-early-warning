"""Convert predicted weekly case counts into Low / Medium / High / Critical risk tiers."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import TIER_LABELS

# Percentiles of the TRAINING target distribution used as tier cut-offs.
TIER_PERCENTILES = (60, 80, 95)


def fit_thresholds(train_target: pd.Series) -> list[float]:
    """Cut-offs learned from training data only (no peeking at validation/test)."""
    qs = np.percentile(train_target, TIER_PERCENTILES)
    # Guarantee strictly increasing, at least 1 case apart (counts are integers)
    out = [max(float(qs[0]), 0.5)]
    for q in qs[1:]:
        out.append(max(float(q), out[-1] + 1))
    return out


def assign_tier(pred, thresholds: list[float]) -> np.ndarray:
    idx = np.digitize(np.asarray(pred, dtype=float), thresholds, right=False)
    return np.array(TIER_LABELS)[idx]
