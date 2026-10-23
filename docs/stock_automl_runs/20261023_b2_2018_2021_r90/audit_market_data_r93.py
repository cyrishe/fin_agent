"""Read-only exposure counts for historical B2 board and minute data issues."""

import json
from pathlib import Path

import pandas as pd

root = Path('/data/cyris/work/b2_2016_2020_r90_20261023')
panel = pd.read_pickle(root / 'panel.pkl')
signals = pd.read_pickle(root / 'signals_exit.pkl')
gate = json.loads((root / 'overall.json').read_text())['gate']
signals = signals.assign(paused=signals.d0_date.str[:7].map(lambda x: gate[x]['paused']))


def counts(frame):
    return {
        'total': int(len(frame)),
        'by_d0_year': {str(y): int(n) for y, n in frame.d0_date.str[:4].value_counts().sort_index().items()},
    }


legacy = lambda f: f.symbol.str.startswith(('300', '301')) & f.d0_date.lt('2020-08-24')
star = lambda f: f.symbol.str.startswith(('688', '689'))
post_growth = lambda f: f.symbol.str.startswith(('300', '301')) & f.d0_date.ge('2020-08-24')

candidate_legacy = panel[legacy(panel) & panel.d0_date.ge('2018-01-01')]
signal_early = signals[signals.d0_date.ge('2018-01-01')]
legacy_signals = signal_early[legacy(signal_early)]
star_signals = signal_early[star(signal_early)]
post_growth_signals = signal_early[post_growth(signal_early)]

out = {
    'panel_rows_2018_2021': int(panel.d0_date.between('2018-01-01', '2021-12-31').sum()),
    'legacy_chinext_d0_candidates': counts(candidate_legacy),
    'legacy_chinext_cap10_confirmed_rows': int(candidate_legacy.rate.eq(.10).sum()),
    'legacy_chinext_would_match_cap20': int((candidate_legacy.close.div(candidate_legacy.preclose).sub(1.2).abs() <= .003).sum()),
    'legacy_chinext_shadow_signals': counts(legacy_signals),
    'legacy_chinext_allowed_signals': counts(legacy_signals[~legacy_signals.paused]),
    'star_d0_candidates': counts(panel[star(panel) & panel.d0_date.ge('2018-01-01')]),
    'star_shadow_signals': counts(star_signals),
    'star_allowed_signals': counts(star_signals[~star_signals.paused]),
    'post_reform_chinext_shadow_signals': counts(post_growth_signals),
    'post_reform_chinext_allowed_signals': counts(post_growth_signals[~post_growth_signals.paused]),
    'minute_d1_extrema_gap_allowed': int((signal_early[~signal_early.paused].extrema_proxy_gap_pct.fillna(0) > 0).sum()),
    'minute_d1_extrema_gap_shadow': int((signal_early.extrema_proxy_gap_pct.fillna(0) > 0).sum()),
}

# Check whether source data marked any of the first five available trading days
# for newly listed STAR / post-reform ChiNext stocks as ordinary limit-up days.
young = []
for path in sorted((root / 'price_months').glob('price_20*.csv.gz')):
    if not ('2019-01' <= path.stem.replace('price_', '')[:7] <= '2021-12'):
        continue
    frame = pd.read_csv(path, usecols=['stk_code', 'trade_date', 'is_limit_price'],
                        dtype={'stk_code': str, 'trade_date': str})
    frame = frame[frame.stk_code.str.startswith(('300', '301', '688', '689'))]
    young.append(frame)
if young:
    young = pd.concat(young).sort_values(['stk_code', 'trade_date'])
    young['first_date'] = young.groupby('stk_code').trade_date.transform('min')
    young['day_index'] = young.groupby('stk_code').cumcount()
    fresh_star = young.stk_code.str.startswith(('688', '689')) & young.first_date.ge('2019-01-01')
    fresh_growth = young.stk_code.str.startswith(('300', '301')) & young.first_date.ge('2020-08-24')
    early = young[(fresh_star | fresh_growth) & young.day_index.lt(5)]
    out['new_listing_first5_source_rows'] = int(len(early))
    out['new_listing_first5_source_limit_flags'] = int(early.is_limit_price.eq(1).sum())
    early = early.assign(symbol=early.stk_code.str[:6])
    early_board = panel.merge(early[['symbol', 'trade_date']], left_on=['symbol', 'd0_date'],
                              right_on=['symbol', 'trade_date'], how='inner')
    out['new_listing_first5_panel_candidates'] = int(len(early_board))

unknown = pd.read_csv(root / 'unknown_st_status.csv', dtype=str)
out['unknown_st_rows'] = int(len(unknown))
out['unknown_st_latest'] = unknown.asof_date.max() if len(unknown) else None
out['unknown_st_unique_stocks'] = int(unknown.symbol.nunique())

dest = root / 'audit_r91' / 'market_data_exposure_r93.json'
dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(out, ensure_ascii=False, indent=2))
