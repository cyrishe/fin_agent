"""R45: predeclared break-even validation gate for the R44 daily choice.

For each target-month B2 signal, use only that month's preceding two-month
validation D1-open-to-D2-close fee-adjusted mean.  When it is <= 0, retain
the paper signal but make no real-account purchase.  This is research only.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import extend_b2_2022_minutes_r41 as r41
from scripts.experiment_b2_daily_search_r43 import RISK_OUT, TEST
from scripts.replay_limit_rebound_cash_r23 import drawdown, dump, replay, sha, sharpe


OUT = Path('outputs/stock_automl/b2_validation_pause_r45_20261019')


def specification() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    value = dict(
        experiment='R45 R44 selected-model validation D2 proxy break-even gate',
        source_strategy='R44 selected daily models + original B2 four K and D2 close exit',
        choice_spec_sha256=sha(RISK_OUT / 'specification.json'),
        selection='For D0 model month M, read the frozen selected model’s two-month validation Top3 activated D1-open-to-D2-close fee-adjusted average. Buy only if strictly >0; otherwise still record the candidate as skipped.',
        threshold='Zero is the break-even sign of the validation proxy, not a fitted threshold.',
        timing='Validation closes before target month. The target-month score, D1 minutes and D2 outcome do not determine whether the month is enabled.',
        account='Original B2 100w, flat half cash or carried all available, D2 close, 0.1% fee each side.',
        caveat='Validation proxy uses D1 opening price; real B2 entry is a later minute-open proxy. This known-history study is not a blind forward result.',
        test_months=TEST, production_changed=False,
    )
    path = OUT / 'specification.json'
    if path.exists():
        assert json.loads(path.read_text()) == value
    else:
        dump(path, value)
    return value


def run() -> None:
    specification()
    calendar = json.loads((r41.OUT / 'calendar.json').read_text())
    source = pd.read_pickle(RISK_OUT / 'signals.pkl')
    source = source[source.d1_date.between(r41.START, r41.END)].copy()
    pass_month = {}
    for month in TEST:
        choice = json.loads((RISK_OUT / 'selected' / month / 'choice.json').read_text())
        assert choice['window']['valid'][1] == str(pd.Period(month) - 1)
        picked = next(x for x in choice['candidates'] if x['id'] == choice['selected'])
        pass_month[month] = dict(model=choice['selected'],
            valid_d2_proxy=float(picked['risk_valid']['mean']),
            valid_samples=int(picked['risk_valid']['n']),
            enabled=bool(picked['risk_valid']['mean'] > 0))
    assert source.d0_date.str[:7].isin(pass_month).all()
    decisions = source[['symbol', 'd0_date', 'd1_date', 'd0_rank', 'entry', 'd2_close']].copy()
    decisions['model_month'] = decisions.d0_date.str[:7]
    decisions['valid_d2_proxy'] = decisions.model_month.map(
        {m: z['valid_d2_proxy'] for m, z in pass_month.items()})
    decisions['validation_enabled'] = decisions.model_month.map(
        {m: z['enabled'] for m, z in pass_month.items()})
    decisions['d2_net'] = decisions.d2_close / decisions.entry * .999 / 1.001 - 1
    kept = source[source.d0_date.str[:7].map({m: z['enabled'] for m, z in pass_month.items()})]
    start, end = r41.START, r41.END
    raw, raw_monthly, raw_nav, _, raw_ex = replay(
        source, calendar, start=start, end=end, fee_rate=.001)
    gated, months, nav, ledger, exclusions = replay(
        kept, calendar, start=start, end=end, fee_rate=.001)
    assert not raw_ex and not exclusions
    assert int(decisions.validation_enabled.sum()) == len(ledger)
    yearly = []
    previous = 1_000_000.
    for year, group in nav.groupby(nav.date.str[:4], sort=True):
        closing = float(group.nav.iloc[-1])
        yearly.append(dict(year=year, opening_nav=previous, closing_nav=closing,
            return_=closing / previous - 1,
            drawdown=drawdown(np.r_[previous, group.nav.to_numpy()]),
            sharpe=sharpe(group.return_),
            buys=int(ledger.entry_date.str.startswith(year).sum()),
            skipped=int((decisions.d1_date.str.startswith(year) &
                         ~decisions.validation_enabled).sum())))
        previous = closing
    dump(OUT / 'overall.json', dict(raw=raw, validation_gate=gated,
        signal_count=len(source), buy_count=len(kept), skip_count=len(source)-len(kept),
        enabled_months=sum(z['enabled'] for z in pass_month.values()),
        disabled_months=sum(not z['enabled'] for z in pass_month.values()),
        source_signal_sha256=sha(RISK_OUT / 'signals.pkl')))
    dump(OUT / 'monthly.json', dict(raw=raw_monthly, validation_gate=months))
    dump(OUT / 'yearly.json', yearly)
    dump(OUT / 'monthly_choices.json', pass_month)
    decisions.to_pickle(OUT / 'decisions.pkl')
    nav.to_pickle(OUT / 'nav.pkl')
    ledger.to_pickle(OUT / 'ledger.pkl')
    print(pd.DataFrame([dict(plan='R44 raw', **raw),
                        dict(plan='R45 validation gate', **gated)])[
        ['plan', 'entries', 'total_return', 'max_drawdown', 'sharpe']].to_string(index=False),
        flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['spec', 'run'])
    arg = parser.parse_args()
    specification() if arg.stage == 'spec' else run()
