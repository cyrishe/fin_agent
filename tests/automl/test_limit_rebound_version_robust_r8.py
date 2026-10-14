import numpy as np
import pandas as pd

from scripts.experiment_limit_rebound_version_robust_r8 import selected


def test_top5_can_be_empty_while_d1_pool_has_a_candidate():
    frame = pd.DataFrame({
        'symbol': ['000001', '000002', '000003', '000004', '000005', '000006'],
        'd0_date': ['2026-04-01'] * 6,
        'd1_date': ['2026-04-02'] * 6,
        'activated': [False, False, False, False, False, True],
        'd1_up': [False] * 6,
        'label_3': [0] * 6,
    })
    scores = np.array([.9, .8, .7, .6, .5, .4])
    assert selected(frame, scores).symbol.tolist() == ['000006']
    assert selected(frame, scores, top5_only=True).empty
