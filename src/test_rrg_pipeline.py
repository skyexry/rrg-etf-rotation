"""Local adapter checks; upstream's own tests validate the original algorithms."""
import numpy as np
import pandas as pd
import pytest

from rrg_pipeline import baseline_weights, verify_upstream, resolve_member_dates


def test_vendor_is_unchanged():
    assert verify_upstream() == '3f83a77f4c7996ed8185b8979a42c4372c1d811f'


def test_baselines_use_only_current_pool_and_observed_history():
    dates = pd.bdate_range('2024-01-01', periods=6)
    close = pd.DataFrame({'A': [10, 11, 12, 13, 14, 15],
                          'B': [10, 10, 10, 10, 10, 10],
                          'C': [np.nan, np.nan, np.nan, 10, 11, 12]}, index=dates)
    panel = pd.DataFrame({'slot1': ['A', 'B'], 'slot2': ['B', 'C']}, index=dates[[2, 5]])
    ew = baseline_weights(panel, close)
    assert ew.loc[dates[2], 'A'] == .5
    assert ew.loc[dates[5], 'A'] == 0
    momentum = baseline_weights(panel, close, lookback=2, top_n=1)
    assert momentum.loc[dates[2], 'A'] == 1
    assert momentum.loc[dates[5], 'C'] == 1
    changed = close.copy()
    changed.loc[dates[3]:] *= 100
    pd.testing.assert_series_equal(momentum.loc[dates[2]],
        baseline_weights(panel, changed, lookback=2, top_n=1).loc[dates[2]])


def test_momentum_configuration_is_explicit():
    with pytest.raises(ValueError):
        baseline_weights(pd.DataFrame(), pd.DataFrame(), lookback=20)


def test_industry_date_convention_preserves_raw_and_applies_only_three_changes():
    records = []
    for code in ['000615.SZ', '000990.SZ', '002254.SZ']:
        records.extend([{'ts_code': code, 'l1_code': 'OLD', 'in_date': pd.Timestamp('2019-12-02'),
                         'out_date': pd.Timestamp('2023-01-03')},
                        {'ts_code': code, 'l1_code': 'NEW', 'in_date': pd.Timestamp('2022-12-13'),
                         'out_date': pd.NaT}])
    raw = pd.DataFrame(records)
    before = raw.copy(deep=True)
    fixed, changes = resolve_member_dates(raw)
    pd.testing.assert_frame_equal(raw, before)
    assert len(changes) == 3
    for date, expected in [('2022-12-30', 'OLD'), ('2023-01-03', 'NEW')]:
        t = pd.Timestamp(date)
        active = fixed[fixed.in_date.le(t) & (fixed.out_date.isna() | fixed.out_date.gt(t))]
        assert len(active) == 3
        assert active.l1_code.eq(expected).all()
