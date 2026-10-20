"""Join every R47 gap2 trade to its exact D0 daily model's two-month validation.

The validation metrics were saved when the R46 model was fitted. This script
only attaches those immutable receipts; it never chooses or refits a model.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.replay_limit_rebound_cash_r23 import dump, sha


R46 = Path('outputs/stock_automl/b2_daily_refit_r46_20261020')
R47 = Path('outputs/stock_automl/b2_daily_shape_r47_20261020')
OUT = Path('outputs/stock_automl/b2_daily_validation_join_r48_20261020')
PLAN = 'gap2_shape2'

HEADERS = [
    'D1日期', 'D0日期', 'D0名次', 'D0评分', '四K精排',
    '四K最高', '四K最低', '四K实体', '买入时间', '买价', '买入涨幅',
    'D2卖日', 'D2卖价', 'D2费后', '原始投入', '影子买入', '影子投入',
    'D1-D3再板', '训练起', '训练止前', '训练样本', '验证起',
    '验证止前', '验开板样本', '验总体再板', '验Top1数', '验Top1中',
    '验Top1再板', 'Top1同日基准', '验Top3数', '验Top3中', '验Top3再板',
    'Top3同日基准', '模型ID',
]


def native(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (np.integer, int, bool, np.bool_)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value)
    return str(value)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ledger = pd.read_pickle(R47 / 'ledger.pkl')
    raw = ledger[ledger.plan.eq(PLAN) & ledger.gate.eq('raw')].sort_values('entry_date')
    shadow = ledger[ledger.plan.eq(PLAN) & ledger.gate.eq('shadow30_10')]
    assert raw.d1_date.is_unique and shadow.d1_date.is_unique
    shadow_by_day = shadow.set_index('d1_date')
    assert set(shadow_by_day.index).issubset(set(raw.d1_date))
    rows = []
    model_ids = set()
    for trade in raw.itertuples():
        root = R46 / 'gap2' / trade.d0_date
        receipt = json.loads((root / 'complete.json').read_text())
        assert receipt['day'] == trade.d0_date and receipt['mode'] == 'gap2'
        assert receipt['test_results_seen'] is False
        assert receipt['score_reload_max_diff'] == 0.0
        assert receipt['train_start'] < receipt['train_end_exclusive'] < trade.d0_date
        ranked = pd.read_pickle(root / 'scores.pkl')
        matched = ranked[ranked.symbol.eq(trade.symbol)]
        assert len(matched) == 1 and matched.d0_date.iloc[0] == trade.d0_date
        assert int(matched.d0_rank.iloc[0]) == int(trade.d0_rank)
        assert np.isclose(float(matched.score.iloc[0]), float(trade.score), atol=1e-12)
        assert np.isclose(float(trade.return_),
                          float(trade.sell / trade.buy * .999 / 1.001 - 1), atol=1e-10)
        v = receipt['valid_open']
        assert v is not None and v['n'] > 0
        t1, t3 = v['top1'], v['top3']
        in_shadow = trade.d1_date in shadow_by_day.index
        shadow_cost = None
        if in_shadow:
            counterpart = shadow_by_day.loc[trade.d1_date]
            assert counterpart.symbol == trade.symbol
            assert np.isclose(float(counterpart.return_), float(trade.return_), atol=1e-12)
            shadow_cost = counterpart.cost
        model_ids.add(receipt['model_sha256'])
        values = [
            trade.d1_date, trade.d0_date, trade.d0_rank, trade.score,
            trade.shape_model, trade.high5, trade.low5, trade.body5,
            trade.entry_time, trade.buy, trade.buy / trade.d1_preclose - 1,
            trade.exit_date, trade.sell, trade.return_, trade.cost,
            int(in_shadow), shadow_cost, trade.label_3,
            receipt['train_start'], receipt['train_end_exclusive'], receipt['train_rows'],
            receipt['train_end_exclusive'], trade.d0_date, v['n'], v['rate'],
            t1['n'], t1['hits'], t1['rate'], t1['same_day_base'],
            t3['n'], t3['hits'], t3['rate'], t3['same_day_base'],
            receipt['model_sha256'][:12],
        ]
        assert len(values) == len(HEADERS)
        rows.append([native(x) for x in values])
    assert len(rows) == len(raw) and len(model_ids) == len(raw)
    assert sum(row[15] for row in rows) == len(shadow)
    payload = {
        'title': 'B2 日更六训两验：逐笔交易与对应日线模型两月验证',
        'note': '一行一笔原始账户实际买入；影子买入=1表示影子停手账户也买入。两月验证仅记录日线模型效果，未用于日线选模；四K精排另行使用验证。验证Top1/Top3均以D0原排名固定且D1开盘打开、标签已成熟为分母。',
        'headers': HEADERS,
        'rows': rows,
        'source': {
            'r46_spec_sha256': sha(R46 / 'specification.json'),
            'r47_spec_sha256': sha(R47 / 'specification.json'),
            'ledger_sha256': sha(R47 / 'ledger.pkl'),
            'raw_trades': len(raw),
            'shadow_trades': len(shadow),
            'unique_daily_models': len(model_ids),
            'validation_used_to_select_daily_model': False,
        },
    }
    dump(OUT / 'workbook_data.json', payload)
    print(OUT / 'workbook_data.json', len(rows), 'rows', flush=True)


if __name__ == '__main__':
    main()
