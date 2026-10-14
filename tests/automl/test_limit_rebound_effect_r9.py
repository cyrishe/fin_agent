import pandas as pd

from scripts.analyze_limit_rebound_effect_r9 import rank_picks
from scripts.export_limit_rebound_d0_top5_r9_detail import exit_gross


def test_d0_top5_is_fixed_before_d1_activation():
    frame = pd.DataFrame({
        'symbol': [f'00000{i}' for i in range(1, 7)] * 2,
        'd0_date': ['2026-04-01'] * 6 + ['2026-04-02'] * 6,
        'd1_date': ['2026-04-02'] * 6 + ['2026-04-03'] * 6,
        'activated': [False] * 5 + [True] + [False] * 3 + [True] * 3,
        'label_3': [0] * 12,
    })
    scores = pd.Series([.9, .8, .7, .6, .5, .4] * 2, index=frame.index)

    top5 = rank_picks(frame, scores, 'forest_sk171', 'd0_top5')
    assert top5[['d1_date', 'symbol', 'd0_rank', 'rank']].values.tolist() == [
        ['2026-04-03', '000004', 4, 1],
        ['2026-04-03', '000005', 5, 2],
    ]
    all_open = rank_picks(frame, scores, 'forest_sk171', 'all_open')
    assert all_open.loc[all_open.d1_date.eq('2026-04-02'), 'symbol'].tolist() == ['000006']


def test_price_returns_use_d1_open_and_d2_d3_close():
    from scripts.analyze_limit_rebound_effect_r9 import price_enriched

    frame = pd.DataFrame({'symbol': ['000001'], 'date_i': [10],
                          'd1_open': [10.0], 'd1_close': [10.5]})
    daily = pd.DataFrame({'symbol': ['000001', '000001'], 'date_i': [12, 13],
                          'close': [11.0, 12.0], 'preclose': [10.5, 11.0]})
    out = price_enriched(frame, daily).iloc[0]
    assert round(out.d2_gross, 6) == .1
    assert round(out.d3_gross, 6) == .2
    assert round(out.corporate_action_gap, 6) == 0


def test_exit_at_first_d2_d4_board_else_d4_close():
    assert exit_gross([True, False, True, True], [True] * 4,
                      [.01, .08, .15]) == .08
    assert exit_gross([False, False, False, False], [True] * 4,
                      [.01, -.02, -.05]) == -.05
    assert exit_gross([False, False, True, None], [True, True, True, False],
                      [.01, .08, None]) == .08
    assert exit_gross([False, False, False, None], [True, True, True, False],
                      [.01, -.02, None]) is None
