"""Verifies the multi-week lag pipeline is correct, per ward, and leak-free.

Run:  python -m pytest -q
"""
import numpy as np
import pandas as pd
import pytest

from src.data import load_raw
from src.features import build_base_features, make_supervised


@pytest.fixture(scope="module")
def raw():
    return load_raw()


@pytest.fixture(scope="module")
def base(raw):
    return build_base_features(raw)


def _ward(df, ward):
    return df[df.ward_id == ward].sort_values("date").reset_index(drop=True)


def test_rain_lags_match_manual_shift(raw, base):
    for ward in raw.ward_id.unique()[:5]:
        r, b = _ward(raw, ward), _ward(base, ward)
        for k in (1, 3, 8):
            expected = r.rainfall_mm.shift(k)
            pd.testing.assert_series_equal(b[f"rain_lag_{k}w"], expected, check_names=False)


def test_rolling_sum_uses_only_past_and_current_weeks(raw, base):
    ward = raw.ward_id.iloc[0]
    r, b = _ward(raw, ward), _ward(base, ward)
    i = 200
    assert np.isclose(b.loc[i, "rain_sum_4w"], r.rainfall_mm.iloc[i - 3 : i + 1].sum())


def test_no_cross_ward_leakage(base):
    # The first rows of every ward must have NaN lags (no value borrowed from the previous ward)
    firsts = base.groupby("ward_id").head(1)
    assert firsts["rain_lag_1w"].isna().all()
    assert firsts["cases_lag_1w"].isna().all()
    eighth = base.groupby("ward_id").nth(7)
    assert eighth["rain_lag_8w"].isna().all()


@pytest.mark.parametrize("h", [2, 3, 4])
def test_target_is_future_cases(raw, h):
    sup = make_supervised(raw, h)
    ward = sup.ward_id.iloc[0]
    r = _ward(raw, ward).set_index("date")
    row = sup[sup.ward_id == ward].iloc[100]
    assert row.target_date == row.date + pd.Timedelta(weeks=h)
    assert row.target == r.loc[row.target_date, "dengue_cases"]
    # Features only use the past: lag-0 rain is the issue week's rain, not the target week's
    assert row.rain_lag_0w == r.loc[row.date, "rainfall_mm"]


def test_last_year_feature_is_before_target(raw):
    sup = make_supervised(raw, 3)
    ward = sup.ward_id.iloc[0]
    r = _ward(raw, ward).set_index("date")
    row = sup[sup.ward_id == ward].iloc[50]
    assert row.cases_same_week_last_year == r.loc[row.target_date - pd.Timedelta(weeks=52), "dengue_cases"]
