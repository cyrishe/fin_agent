import numpy as np
import pandas as pd

from scripts.research_kline_forward_kp1 import outcomes, board, chained_bars


def test_outcomes_start_after_signal_and_exclude_signal_high_low():
    f = pd.DataFrame({'valid': [1]*4, 'c': [100., 101., 102., 99.],
                      'o': [98., 100.5, 101., 100.],
                      'h': [999., 103., 104., 101.], 'l': [1., 99., 100., 97.]})
    y = outcomes(f)
    np.testing.assert_allclose(y.loc[0, ['r1', 'r2', 'r3']], [1, 2, -1])
    np.testing.assert_allclose(y.loc[0, ['mfe3', 'mae3']], [4, -3])
    assert np.isclose(y.loc[0, 'nextopen_r2'], (102/100.5-1)*100)
    assert pd.isna(y.loc[1, 'r3'])


def test_suspension_does_not_compress_horizon_or_become_loss():
    f = pd.DataFrame({'valid': [1, 0, 1, 1], 'c': [100., np.nan, 102., 103.],
                      'o': [100., np.nan, 101., 102.],
                      'h': [101., np.nan, 103., 104.], 'l': [99., np.nan, 100., 101.]})
    y = outcomes(f)
    assert y.loc[0, ['r1', 'r2', 'r3', 'mfe3', 'mae3']].isna().all()
    assert np.isclose(y.loc[2, 'r1'], (103/102-1)*100)


def test_universe_is_explicit_sh_sz_equities():
    assert board('600000.SH') == 'SH_main'
    assert board('688001.SH') == 'STAR'
    assert board('300001.SZ') == 'ChiNext'
    assert board('000001.SZ') == 'SZ_main'
    assert board('000001.SH') is None
    assert board('920001.BJ') is None


def test_reference_close_chain_handles_exrights_without_provider_factor_jump():
    b = pd.DataFrame({'open': [100., 50., 52.], 'high': [101., 53., 53.],
                      'low': [99., 49., 50.], 'close': [100., 52., 51.],
                      'preclose': [100., 50., 52.], 'volume': [1000.]*3, 'turn_ratio': [1.]*3})
    p = chained_bars(b)
    np.testing.assert_allclose(p.close, [100, 104, 102])
    np.testing.assert_allclose(p.high / p.close, b.high / b.close)
    pd.testing.assert_frame_equal(chained_bars(b.iloc[:2]), p.iloc[:2])
    b.loc[1, 'preclose'] = np.nan
    assert chained_bars(b).loc[1, ['open','high','low','close']].isna().all()
