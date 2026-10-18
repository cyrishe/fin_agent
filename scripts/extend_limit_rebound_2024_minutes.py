"""Extend the frozen R18/R19 five-minute study into newly testable 2025 months.

The new 2024 minute bars permit the same six-train/two-valid/one-test rule
selection for 2025-05..08.  2025-09 is rebuilt as an unchanged-control gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from scripts.experiment_limit_rebound_monthly_r14 import block
from scripts.experiment_limit_rebound_segments_r18 import FEATURES, OUT as R18, rank, window
from scripts.experiment_limit_rebound_short_mixed_r12 import dump, make_model, sha, xmatrix
from scripts.experiment_automl_limit_rebound_st_size_r4 import balanced_weights
from scripts.export_limit_rebound_short_mixed_r12 import filtered_local
from scripts.mk_minute_archive import COMPACT_ROOT_2024, COMPACT_ROOT_2025, day_file
from scripts.experiment_limit_rebound_shape_top3_r20 import select, stats
from scripts.replay_limit_rebound_body8_r21 import body_keep
from scripts.replay_limit_rebound_cash_r23 import replay
import scripts.experiment_limit_rebound_shape_r19 as r19


OUT = Path('outputs/stock_automl/limit_rebound_2024_minute_extension_20261017')
MINUTE_2024 = COMPACT_ROOT_2024
R19 = Path('outputs/stock_automl/limit_rebound_shape_r19_20261015')
MONTHS = ['2025-05', '2025-06', '2025-07', '2025-08', '2025-09']
EARLY = ['2024-09', '2024-10', '2024-11', '2024-12']
KEY = ['symbol', 'd0_date']


def early_forecasts(panel: pd.DataFrame, calendar: list[str]) -> None:
    for month in EARLY:
        root = OUT / 'early_models' / month
        root.mkdir(parents=True, exist_ok=True)
        model_path = root / 'segments.joblib'
        score_path = root / 'segments_forecast_scores.pkl'
        if model_path.exists() and score_path.exists():
            continue
        train = block(panel, calendar, *window(month)['train'])
        fit = train[train.activated & train.observed_3]
        features = FEATURES['segments']
        model = make_model('rf')
        model.steps[-1][1].n_jobs = 1
        model.fit(xmatrix(fit, features), fit.label_3,
                  randomforestclassifier__sample_weight=balanced_weights(fit.label_3))
        bundle = {'model': model, 'features': features, 'month': month,
                  'window': window(month), 'sklearn': sklearn.__version__,
                  'training_keys': fit[['symbol', 'd0_date', 'first_limit_date']].to_dict('records'),
                  'parent_panel_sha256': sha(R18 / 'panel.pkl')}
        joblib.dump(bundle, model_path, compress=3)
        restored = joblib.load(model_path)
        test = panel[panel.d0_date.str.startswith(month)]
        scored = rank(test, restored['model'].predict_proba(xmatrix(test, features))[:, 1])
        scored[KEY + ['score', 'd0_rank']].to_pickle(score_path)
        print('early stage-one', month, len(fit), len(test), flush=True)


def score_path(month: str) -> Path:
    if month in EARLY:
        return OUT / 'early_models' / month / 'segments_forecast_scores.pkl'
    return R18 / month / 'segments_forecast_scores.pkl'


def candidate_rows(panel: pd.DataFrame, calendar: list[str]) -> pd.DataFrame:
    parts = []
    for test_month in MONTHS:
        w = window(test_month)
        train_months = [str(m) for m in pd.period_range(*w['train'], freq='M')]
        allowed = block(panel, calendar, *w['train'])[KEY]
        train = pd.concat([pd.read_pickle(score_path(m)) for m in train_months], ignore_index=True)
        train = train.merge(allowed, on=KEY, validate='one_to_one')
        for part, scored in [('train', train),
                             ('valid', pd.read_pickle(R18 / test_month / 'segments_valid_scores.pkl')),
                             ('test', pd.read_pickle(score_path(test_month)))]:
            selected = scored[scored.d0_rank.eq(1)].merge(panel, on=KEY, validate='one_to_one')
            selected['model_month'] = test_month
            selected['part'] = part
            parts.append(selected)
    result = pd.concat(parts, ignore_index=True)
    result.to_pickle(OUT / 'all_top1.pkl')
    return result


def daily_high_low(demand: pd.DataFrame) -> pd.DataFrame:
    manifest = json.loads((R18 / 'source_manifest.json').read_text())
    months = set(demand.d1_date.dropna().str[:7])
    wanted = set(demand.symbol)
    chunks = []
    for item in manifest['raw_sources']:
        path = Path(item['path'])
        if not any(f'price_{month}' in path.name for month in months):
            continue
        if sha(path) != item['sha256']:
            raise ValueError(f'Changed daily source: {path}')
        daily = pd.read_csv(path, dtype={'stk_code': str, 'trade_date': str},
                            usecols=['stk_code', 'trade_date', 'high', 'low'])
        daily['symbol'] = daily.stk_code.str[:6]
        chunks.append(daily[daily.symbol.isin(wanted)])
    quotes = pd.concat(chunks, ignore_index=True).drop_duplicates(
        ['symbol', 'trade_date'], keep='last')
    return quotes[['symbol', 'trade_date', 'high', 'low']].rename(
        columns={'trade_date': 'd1_date', 'high': 'd1_high', 'low': 'd1_low'})


def with_status(demand: pd.DataFrame) -> pd.DataFrame:
    hist = pd.read_csv(R19 / 'historical_status.csv.gz', dtype=str)
    hist['symbol'] = hist.stk_code.str[:6]
    groups = {symbol: group for symbol, group in hist.groupby('symbol')}
    for which in (0, 1):
        states = []
        for row in demand.itertuples():
            day = getattr(row, f'd{which}_date')
            group = groups.get(row.symbol)
            if group is None or pd.isna(day):
                states.append('unknown')
                continue
            matched = group[group.begin_date.le(day) & group.end_date.gt(day)]
            states.append(matched.st_type.iloc[0] if len(matched) == 1 else 'unknown')
        demand[f'state{which}'] = states
    demand['ordinary'] = demand.state0.isin(['N', 'R']) & demand.state1.isin(['N', 'R'])
    return demand


def minute_facts(demand: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for index, (day, group) in enumerate(demand.dropna(subset=['d1_date']).groupby('d1_date'), 1):
        root = MINUTE_2024 if day.startswith('2024') else COMPACT_ROOT_2025
        try:
            path = day_file(root, day)
            bars = filtered_local(path, set(group.symbol))
        except FileNotFoundError:
            path = None
            bars = pd.DataFrame()
        by_symbol = {s: b for s, b in bars.groupby(bars.symbol.str[:6])} if len(bars) else {}
        for row in group.itertuples():
            value = r19.facts(by_symbol.get(row.symbol, pd.DataFrame()), row)
            rows.append({'symbol': row.symbol, 'd1_date': day, 'minute_source': str(path) if path else None,
                         **value})
        if index % 20 == 0:
            print('minute days', index, flush=True)
    return pd.DataFrame(rows)


def enrich(candidates: pd.DataFrame) -> pd.DataFrame:
    demand = candidates.drop_duplicates(KEY).copy()
    demand = demand.merge(daily_high_low(demand), on=['symbol', 'd1_date'], how='left',
                          validate='one_to_one')
    demand = with_status(demand)
    minute = minute_facts(demand)
    minute.to_pickle(OUT / 'minute_facts.pkl')
    result = candidates.merge(demand[KEY + ['state0', 'state1', 'ordinary', 'd1_high', 'd1_low']],
                              on=KEY, validate='many_to_one')
    result = result.merge(minute, on=['symbol', 'd1_date'], how='left', validate='many_to_one')
    result['eligible'] = result.ordinary & result.audit_ok.eq(True)
    result['trade'] = result.eligible & result['fill'].eq(True)
    continuous = result.d1_close.gt(0)
    for k in (2, 3, 4):
        continuous &= result[f'd{k}_close'].gt(0) & result[f'd{k}_preclose'].sub(
            result[f'd{k-1}_close']).abs().le(.011)
        result[f'ret{k}'] = np.where(result.trade & continuous,
                                     result[f'd{k}_close'] / result.entry - 1, np.nan)
    result['strategy'] = np.where(result.d2_up.eq(True), result.ret2,
                                  np.where(result.d3_up.eq(True), result.ret3, result.ret4))
    result['complete'] = result.trade & result.observed_3 & result.ret4.notna()
    result.to_pickle(OUT / 'enriched_top1.pkl')
    return result


def best_strategy_extension(calendar: list[str]) -> dict:
    """Apply the existing Top3, body and fee rules to the newly frozen months."""
    source = pd.read_pickle(Path(
        'outputs/stock_automl/limit_rebound_body8_nofallback_r21b_20261015/all_candidates.pkl'))
    original_rules = {row['month']: row['selected'] for row in json.loads(
        (R19 / 'all_rules_frozen.json').read_text())}
    control = source[source.d0_date.ge('2026-01-01')].copy()
    control['shape_pass'] = False
    for month, rule in original_rules.items():
        selected = control.d0_date.str.startswith(month)
        if selected.any():
            control.loc[selected, 'shape_pass'] = (control.loc[selected, 'eligible']
                                                    & r19.mask(control.loc[selected], rule))
    if not control.shape_pass.equals(source.loc[control.index, 'shape_pass']):
        raise AssertionError('Original 2026 shape masks did not reproduce')
    control_pick = select(control, 3, True)
    control_pick = control_pick[body_keep(control_pick.body5)]
    reference = pd.read_pickle(
        'outputs/stock_automl/limit_rebound_fee001_r23c_20261015/signals.pkl')
    keys = ['symbol', 'd0_date', 'd1_date', 'entry_time', 'entry']
    pd.testing.assert_frame_equal(control_pick[keys].sort_values('d0_date').reset_index(drop=True),
                                  reference[keys].sort_values('d0_date').reset_index(drop=True),
                                  check_dtype=False)
    control_account, _, _, _, _ = replay(control_pick, calendar, fee_rate=.001)
    reference_account = json.loads(Path(
        'outputs/stock_automl/limit_rebound_fee001_r23c_20261015/summary.json').read_text())
    if not np.isclose(control_account['final_nav'], reference_account['final_nav'], atol=1e-7):
        raise AssertionError('Original R23C fee account did not reproduce')

    frozen = {row['month']: row['selected'] for row in json.loads(
        (OUT / 'all_rules_frozen.json').read_text())}
    early = source[source.d0_date.str[:7].isin(MONTHS[:-1])].copy()
    early['shape_pass'] = False
    for month in MONTHS[:-1]:
        selected = early.d0_date.str.startswith(month)
        early.loc[selected, 'shape_pass'] = (early.loc[selected, 'eligible']
                                              & r19.mask(early.loc[selected], frozen[month]))
    shaped = select(early, 3, True)
    picked = shaped[body_keep(shaped.body5)].copy()
    unshaped = select(early, 3, False)
    comparison = unshaped[body_keep(unshaped.body5)].copy()
    picked.to_pickle(OUT / 'new_month_signals.pkl')
    early.to_pickle(OUT / 'new_month_candidates.pkl')
    end = '2025-09-05'  # Includes next-day exits for late-August D0 signals.
    results = {}
    for name, signals in [('frozen_shape_body8', picked), ('no_shape_body8', comparison)]:
        summary, monthly, nav, ledger, excluded = replay(
            signals, calendar, start='2025-05-01', end=end, fee_rate=.001)
        nav.to_pickle(OUT / f'{name}_nav.pkl')
        ledger.to_pickle(OUT / f'{name}_ledger.pkl')
        results[name] = {'account': summary, 'monthly': monthly,
                         'classification': stats(signals), 'excluded': excluded}
    return {'control_signals': len(control_pick), 'control_nav': control_account['final_nav'],
            'control_reproduced': True, 'new_candidate_rows': len(early),
            'new_shape_signals': len(picked), 'new_no_shape_signals': len(comparison),
            'results': results}


def run() -> dict:
    if sklearn.__version__ != '1.7.1':
        raise RuntimeError('Run with the original scikit-learn 1.7.1 environment')
    imported = json.loads((MINUTE_2024 / 'manifest.json').read_text())
    calendar = json.loads((R18 / 'calendar.json').read_text())
    required = {day for day in calendar if '2024-09-01' <= day <= '2024-12-31'}
    archived = {row['day'] for row in imported['rows']}
    if not required.issubset(archived):
        raise RuntimeError('2024 September–December minute archive is incomplete')
    OUT.mkdir(parents=True, exist_ok=True)
    panel = pd.read_pickle(R18 / 'panel.pkl')
    early_forecasts(panel, calendar)
    result = enrich(candidate_rows(panel, calendar))
    r19.OUT, r19.TEST_MONTHS = OUT, MONTHS
    r19.choose()
    r19.report()
    original = json.loads((R19 / '2025-09' / 'rule_frozen.json').read_text())
    reproduced = json.loads((OUT / '2025-09' / 'rule_frozen.json').read_text())
    if original['selected'] != reproduced['selected']:
        raise AssertionError('2025-09 unchanged-control rule did not reproduce')
    original_metrics = json.loads((R19 / 'monthly_metrics.json').read_text())
    new_metrics = json.loads((OUT / 'monthly_metrics.json').read_text())
    original_test = [row for row in original_metrics
                     if row['月份'] == '2025-09' and row['集合'] == 'test']
    new_test = [row for row in new_metrics
                if row['月份'] == '2025-09' and row['集合'] == 'test']
    if original_test != new_test:
        raise AssertionError('2025-09 unchanged-control test metrics did not reproduce')
    trading = best_strategy_extension(calendar)
    summary = {'new_test_months': MONTHS[:-1], 'control_month': MONTHS[-1],
               'control_rule_reproduced': True, 'control_metrics_reproduced': True,
               'imported_days': len(required), 'archive_days': imported['days'],
               'input_hashes': {
                   'r18_panel': sha(R18 / 'panel.pkl'),
                   'minute_2024_manifest': sha(MINUTE_2024 / 'manifest.json'),
                   'original_r23c_signals': sha(Path(
                       'outputs/stock_automl/limit_rebound_fee001_r23c_20261015/signals.pkl')),
                   'early_models': {m: sha(OUT / 'early_models' / m / 'segments.joblib') for m in EARLY},
               },
               'candidate_rows': len(result),
               'minute_verified': int(result.drop_duplicates(KEY).audit_ok.eq(True).sum()),
               'minute_failed_reasons': result.drop_duplicates(KEY).loc[
                   ~result.drop_duplicates(KEY).audit_ok.eq(True), 'reason'].value_counts().to_dict(),
               'original_r19_rule_sha256': sha(R19 / '2025-09' / 'rule_frozen.json'),
               'new_rules_sha256': sha(OUT / 'all_rules_frozen.json'),
               'trading': trading}
    dump(OUT / 'extension_summary.json', summary)
    return summary


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False, indent=2), flush=True)
