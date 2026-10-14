"""Paired day-level Top1 comparison for the R7 matched candidate pools."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


OUT = Path('outputs/stock_automl/limit_rebound_1430_r7_20261014')


def daily_top1(path: Path, label: str) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={'symbol': str, 'd1_date': str})
    return (frame.sort_values(['d1_date', 'score', 'symbol'], ascending=[True, False, True])
            .groupby('d1_date').head(1)[['d1_date', label]]
            .rename(columns={label: 'hit'}))


def run(out: Path = OUT) -> dict:
    result = {}
    for target, label in (('next1', 'label_next1'), ('next3', 'label_next3'),
                          ('next3_after_cold_close', 'label_next3_after_cold_close')):
        result[target] = {}
        for part in ('valid', 'test'):
            prior = daily_top1(out / f'{target}_d0_only_{part}.csv.gz', label)
            minute = daily_top1(out / f'{target}_d0_plus_1430_{part}.csv.gz', label)
            paired = prior.merge(minute, on='d1_date', validate='one_to_one', suffixes=('_prior', '_minute'))
            if len(paired) != len(prior) or len(paired) != len(minute):
                raise ValueError('Model top1 dates do not match')
            delta = paired.hit_minute.to_numpy(dtype=int) - paired.hit_prior.to_numpy(dtype=int)
            rng = np.random.default_rng(20261014)
            samples = rng.choice(delta, size=(10000, len(delta)), replace=True).sum(axis=1)
            result[target][part] = {
                'days': len(delta), 'prior_hits': int(paired.hit_prior.sum()),
                'minute_hits': int(paired.hit_minute.sum()),
                'net_additional_hits': int(delta.sum()),
                'minute_only_hits': int((delta == 1).sum()),
                'prior_only_hits': int((delta == -1).sum()),
                'bootstrap_95pct_additional_hits': np.quantile(samples, [.025, .975]).tolist(),
                'by_month': {month: {'prior': int(g.hit_prior.sum()), 'minute': int(g.hit_minute.sum()),
                                     'days': len(g)}
                             for month, g in paired.groupby(paired.d1_date.str[:7])},
            }
    (out / 'paired_top1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    run()
