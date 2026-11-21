"""Central configuration: paths, horizons, chronological split, risk tiers."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_CSV = ROOT / "data" / "raw" / "dengue.csv"
MODELS_DIR = ROOT / "models"
METRICS_JSON = MODELS_DIR / "metrics.json"

# Forecast horizons in weeks: the 2-to-4-week early-warning window from the brief.
HORIZONS = (2, 3, 4)

# Chronological split, by the year of the TARGET week (the week being predicted).
# Never shuffle: the model must only learn from the past and be tested on the future.
TRAIN_END_YEAR = 2022   # train: 2017-2022 (2016 is used only as lag history)
VAL_YEAR = 2023         # validation: model selection + early stopping
TEST_YEARS = (2024, 2025)  # held-out test: reported once, never used for tuning

# Risk tiers on predicted weekly cases per ward.
# Cut-offs come from the training-set distribution of weekly cases:
# ~60th, ~80th and ~95th percentiles (see notebooks / ARCHITECTURE.md).
TIER_LABELS = ("Low", "Medium", "High", "Critical")
TIER_COLORS = {"Low": "#2e7d32", "Medium": "#f9a825", "High": "#ef6c00", "Critical": "#c62828"}

RANDOM_STATE = 42

# Deployed model uses weather + ward vulnerability + season only.
# Ablation (see metrics.json) shows adding past case counts gives no test-set gain, and a
# weather-only model keeps working when hospital case reports arrive late.
USE_CASE_HISTORY = False
