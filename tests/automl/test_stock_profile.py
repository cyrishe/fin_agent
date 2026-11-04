import pandas as pd
import pytest

from scripts.audit_automl_stock_profile import build_profile


def sources():
    dates = pd.date_range("2026-09-01", periods=12, freq="D")
    price = pd.DataFrame([{
        "date": day, "symbol6": "600001", "volume": (index + 1) * 1_000_000,
        "amount": (index + 1) * 10_000_000, "turn_ratio": float(index + 1),
        "create_time": f"{day.date()} 16:00:00",
        "update_time": f"{day.date()} 16:00:00",
    } for index, day in enumerate(dates)])
    value = pd.DataFrame([{
        "date": day, "symbol6": "600001", "total_mv": (index + 1) * 1e8,
        "free_float_mv": (index + 1) * 5e7,
        "create_time": f"{day.date()} 16:00:00",
        "update_time": f"{day.date()} 16:00:00",
    } for index, day in enumerate(dates)])
    return dates, price, value


def test_profile_uses_previous_ten_dates_and_previous_day_cap_only():
    dates, price, value = sources()
    result = build_profile(price, value)
    signal = result[result.signal_date.eq(dates[10])].iloc[0]
    assert signal.profile_through_date == dates[9]
    assert signal.value_through_date == dates[9]
    assert signal.avg_volume_5_million == pytest.approx(8)
    assert signal.avg_volume_10_million == pytest.approx(5.5)
    assert signal.avg_turn_5_pct == pytest.approx(8)
    assert signal.total_mv_100m_cny == pytest.approx(10)
    assert signal.price_profile_ready
    assert signal.total_mv_ready


def test_late_revision_or_untraded_history_is_not_asof_ready():
    dates, price, value = sources()
    late_price = price.copy()
    late_price.loc[9, "update_time"] = f"{dates[10].date()} 15:00:00"
    late_value = value.copy()
    late_value.loc[9, "update_time"] = f"{dates[10].date()} 15:00:00"
    row = build_profile(late_price, late_value)
    row = row[row.signal_date.eq(dates[10])].iloc[0]
    assert not row.price_profile_ready
    assert not row.total_mv_ready
    assert pd.isna(row.avg_volume_5_million)
    assert pd.isna(row.total_mv_100m_cny)
    untraded = price.copy()
    untraded.loc[5, "volume"] = 0
    row = build_profile(untraded, value)
    assert not row[row.signal_date.eq(dates[10])].iloc[0].price_profile_ready
