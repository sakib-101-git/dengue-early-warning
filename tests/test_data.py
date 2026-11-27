"""Phase 2 dataset: the onsite CSV only ADDS supply columns, so features and forecasts don't change."""
import pytest

from src import config
from src.data import SUPPLY_COLUMNS, load_raw


def test_legacy_csv_still_loads_without_supply_columns():
    df = load_raw(config.LEGACY_CSV)
    assert df.shape == (31320, 12)
    assert not set(SUPPLY_COLUMNS) & set(df.columns)


def test_raw_csv_prefers_onsite_file():
    expected = config.ONSITE_CSV if config.ONSITE_CSV.exists() else config.LEGACY_CSV
    assert config.RAW_CSV == expected


@pytest.mark.skipif(not config.ONSITE_CSV.exists(), reason="onsite CSV not present")
def test_onsite_base_columns_identical_to_legacy():
    old, new = load_raw(config.LEGACY_CSV), load_raw(config.ONSITE_CSV)
    assert set(SUPPLY_COLUMNS) <= set(new.columns)
    assert new[old.columns].equals(old)  # same rows, same order, same values -> same features
    assert (new[list(SUPPLY_COLUMNS)] >= 0).all().all()
