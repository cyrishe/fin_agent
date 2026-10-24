"""Compare R104 QDC replay with the archived B2_V2 source runs."""
from __future__ import annotations

import json
import pandas as pd

from scripts.experiment_b2_recent_loss_resume_r55 import prior_return, should_pause
from scripts.replay_limit_rebound_cash_r23 import replay
from scripts.research_b2_daily_only_r100 import read_inputs
from scripts.research_b2_qdc_r104 import OUT, ROOT, digest


SOURCES = [
    ('docs/stock_automl_runs/20261023_b2_2018_2021_r90/evidence/signals_exit.pkl', '2017-05', '2021-12'),
    ('outputs/stock_automl/b2_2021_minute_r60_20261020/signals.pkl', '2022-05', '2023-04'),
    ('outputs/stock_automl/b2_2022_minute_r41_20261019/signals.pkl', '2023-05', '2024-02'),
    ('outputs/stock_automl/b2_two_month_2024_r35_20261019/signals.pkl', '2024-03', '2026-09'),
]


def old_signals(calendar: list[str]) -> pd.DataFrame:
    parts = []
    for path, first, last in SOURCES:
        frame = pd.read_pickle(ROOT / path)
        month = frame.d0_date.str[:7]
        parts.append(frame[month.ge(first) & month.le(last)])
    result = pd.concat(parts, ignore_index=True)
    result = result[result.d1_date.le('2026-09-30') & result.d2_close.notna()].copy()
    result['model_month'] = result.d0_date.str[:7]
    index = {day: i for i, day in enumerate(calendar)}
    result['d2_date'] = result.d1_date.map(lambda d: calendar[index[d] + 1])
    result['d2_cash_dividend'] = result.d2_cash_dividend.fillna(0)
    # R90 has two ex dates before purchase; they do not affect the bought shares.
    before_buy = result.close.sub(result.d1_preclose).abs().gt(.011)
    assert int(before_buy.sum()) == 2
    result.loc[before_buy, 'close'] = result.loc[before_buy, 'd1_preclose']
    # One independently checked 2021 held cash dividend is part of R90's account.
    held_dividend = result.d2_cash_dividend.gt(0)
    assert int(held_dividend.sum()) == 1
    result.loc[held_dividend, 'd2_preclose'] = result.loc[held_dividend, 'd1_close']
    result.loc[held_dividend, 'd2_close'] += result.loc[held_dividend, 'd2_cash_dividend']
    return result


def gates(signals: pd.DataFrame, calendar: list[str]) -> pd.DataFrame:
    _, _, nav, _, excluded = replay(signals, calendar, start='2017-01-01',
                                    end=max(signals.d2_date), fee_rate=.001)
    assert not excluded, excluded[:3]
    nav = nav.set_index('date')
    months = sorted(signals.model_month.unique())
    counts = signals.groupby('model_month').size().to_dict()
    rows = []
    for month in months:
        prior = str(pd.Period(month) - 1)
        r5, r10 = prior_return(nav, prior, 5), prior_return(nav, prior, 10)
        n = counts.get(prior) if prior in months else None
        rows.append(dict(model_month=month, prior_month=prior, prior_candidates=n,
                         prior_r5=r5, prior_r10=r10,
                         paused=bool(should_pause(n, r5, r10, 'sum_negative')) if n is not None else False,
                         prior_available=n is not None))
    return pd.DataFrame(rows)


def yearly(signals: pd.DataFrame, calendar: list[str], gate: pd.DataFrame,
           name: str) -> pd.DataFrame:
    paused = set(gate.loc[gate.paused, 'model_month'])
    rows = []
    for year in range(2018, 2027):
        year_frame = signals[signals.model_month.str[:4].eq(str(year))]
        if year_frame.empty:
            continue
        selected = year_frame[~year_frame.model_month.isin(paused)]
        result, _, _, _, excluded = replay(selected, calendar, start=f'{year}-01-01',
                                           end=max(selected.d2_date), fee_rate=.001)
        assert not excluded, (year, excluded[:3])
        rows.append(dict(year=year, source=name, signals=len(selected),
                         return_=result['total_return'], drawdown=result['max_drawdown'],
                         win_rate=result['closed_win_rate'],
                         paused_months=int(gate[gate.model_month.str[:4].eq(str(year))].paused.sum())))
    return pd.DataFrame(rows)


def overlap(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    old = old.copy(); new = new.copy()
    old['key'] = old.d0_date + '_' + old.symbol
    new['key'] = new.d0_date + '_' + new.symbol
    rows = []
    for year in range(2017, 2027):
        a = old[old.d0_date.str[:4].eq(str(year))]
        b = new[new.d0_date.str[:4].eq(str(year))]
        both = set(a.key) & set(b.key)
        matched = a[a.key.isin(both)][['key', 'entry']].merge(
            b[b.key.isin(both)][['key', 'entry']], on='key', suffixes=('_old', '_qdc'))
        rows.append(dict(year=year, old_signals=len(a), qdc_signals=len(b), overlap=len(both),
                         old_only=len(set(a.key) - set(b.key)), qdc_only=len(set(b.key) - set(a.key)),
                         common_entry_diff_over_1c=int(matched.entry_old.sub(matched.entry_qdc).abs().gt(.011).sum())))
    return pd.DataFrame(rows)


def missing_d2_proxy(signals: pd.DataFrame, calendar: list[str]) -> pd.DataFrame:
    """Optimistic price-only sensitivity; does not establish D2 sellability."""
    candidates = pd.read_pickle(OUT / 'candidates.pkl')
    decisions = pd.read_csv(OUT / 'decisions.csv', dtype={'symbol': str})
    decisions = decisions[decisions.actionable.eq(1)][['symbol', 'd0_date', 'entry_price', 'entry_at']]
    missing = candidates.merge(decisions, on=['symbol', 'd0_date'], validate='one_to_one')
    missing = missing[missing.qdc_d0_close.notna() & missing.qdc_d1_close.notna() &
                      missing.qdc_d2_close.isna() & missing.d2_close.notna()].copy()
    assert len(missing) == 11
    missing['entry'] = missing.entry_price
    missing['close'] = missing.qdc_d0_close
    missing['d1_preclose'] = missing.qdc_d0_close
    missing['d1_close'] = missing.qdc_d1_close
    missing['d2_preclose'] = missing.qdc_d1_close
    missing['rule_id'] = 'proxy_missing_d2'
    missing['qdc_preclose_proxy'] = missing.qdc_d0_close
    combined = pd.concat([signals, missing[signals.columns]], ignore_index=True)
    return yearly(combined, calendar, gates(combined, calendar), 'old_stale_close_proxy')


def main() -> None:
    _, calendar, _ = read_inputs()
    (OUT / 'original_signal_sources.json').write_text(json.dumps(
        {path: digest(ROOT / path) for path, _, _ in SOURCES},
        ensure_ascii=False, indent=2) + '\n')
    old = old_signals(calendar)
    new = pd.read_pickle(OUT / 'signals.pkl')
    original_gate = gates(old, calendar)
    original_gate.to_csv(OUT / 'original_gates_check.csv', index=False)
    old_year = yearly(old, calendar, original_gate, 'old')
    old_year.to_csv(OUT / 'original_yearly_check.csv', index=False)
    new_gate = pd.read_csv(OUT / 'gates.csv')
    new_year = yearly(new, calendar, new_gate, 'qdc')
    prior = pd.read_csv(OUT / 'yearly.csv').query("scenario=='V2' and year>=2018")
    assert new_year[['year', 'signals']].reset_index(drop=True).equals(
        prior[['year', 'signals']].reset_index(drop=True))
    comparison = old_year.merge(new_year, on='year', suffixes=('_old', '_qdc'))
    comparison.to_csv(OUT / 'year_comparison.csv', index=False)
    overlap(old, new).to_csv(OUT / 'signal_overlap.csv', index=False)
    fixed_gate = yearly(new, calendar, original_gate, 'qdc_old_gate')
    fixed_gate.to_csv(OUT / 'qdc_old_gate_yearly.csv', index=False)
    merged_gates = new_gate.merge(original_gate[['model_month', 'paused']], on='model_month',
                                  suffixes=('_qdc', '_old'))
    merged_gates.loc[merged_gates.paused_qdc.ne(merged_gates.paused_old)].to_csv(
        OUT / 'gate_flips.csv', index=False)
    missing_d2_proxy(new, calendar).to_csv(OUT / 'missing_d2_old_close_proxy.csv', index=False)
    assert abs(float(old_year.loc[old_year.year.eq(2019), 'return_'].iloc[0]) + .2311) < .0002
    assert abs(float(old_year.loc[old_year.year.eq(2021), 'return_'].iloc[0]) + .1437) < .0002
    print(comparison[['year', 'signals_old', 'signals_qdc', 'return__old', 'return__qdc',
                      'drawdown_old', 'drawdown_qdc']].to_string(index=False))


if __name__ == '__main__':
    main()
