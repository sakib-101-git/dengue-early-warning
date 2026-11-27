"""Load and validate the raw weekly ward-level dataset."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.config import RAW_CSV

REQUIRED_COLUMNS = {
    "date": "datetime64",
    "city": "object",
    "ward_id": "object",
    "population_density_sqkm": "number",
    "water_logging_risk_index": "number",
    "sanitation_score": "number",
    "avg_temp_c": "number",
    "max_temp_c": "number",
    "min_temp_c": "number",
    "rainfall_mm": "number",
    "relative_humidity_pct": "number",
    "dengue_cases": "number",
}

# Phase 2 supply columns (onsite dataset only). Optional: the Phase 1 file has none of them.
SUPPLY_COLUMNS = ("saline_bags_needed", "diagnostic_kits_needed", "hospital_beds_required")


def load_raw(path: str | Path = RAW_CSV) -> pd.DataFrame:
    """Read the CSV, normalise column names, validate schema, sort by ward and date."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]  # brief writes avg_temp_C

    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Dataset is missing columns: {sorted(missing)}")

    df["date"] = pd.to_datetime(df["date"])
    df["city"] = df["city"].astype(str)
    df["ward_id"] = df["ward_id"].astype(str)
    for c in SUPPLY_COLUMNS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    if df.duplicated(["ward_id", "date"]).any():
        raise ValueError("Duplicate (ward_id, date) rows found")
    if df.isna().any().any():
        # Weather gaps: forward-fill within each ward so a lag never borrows another ward's value.
        num = df.select_dtypes("number").columns
        df[num] = df.groupby("ward_id")[num].ffill().fillna(0)

    return df.sort_values(["ward_id", "date"]).reset_index(drop=True)
